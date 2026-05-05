from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from research.interface.research_interface import (
    cancel_task,
    create_research_task,
    get_task_detail,
    get_task_events,
    get_task_status_view,
    get_task_step_logs,
    recover_research_tasks,
)
from research.models import (
    DISPATCH_CANCELLED,
    DISPATCH_FAILED,
    DISPATCH_FINISHED,
    DISPATCH_PENDING,
    DISPATCH_QUEUED,
    ResearchTask,
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_FAILED,
    TaskStepLog,
)
from research.task_runner import ResearchTaskRunner


@override_settings(
    DATABASES={'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}}
)
class ResearchTaskFlowTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='runner-user',
            email='runner@example.com',
            password='password123!'
        )

    def test_create_task_persists_pending_state_and_initial_log(self):
        with mock.patch('research.interface.research_interface.ResearchTaskRunner.enqueue', return_value=True) as enqueue_mock:
            with self.captureOnCommitCallbacks(execute=True):
                success, error, task_id = create_research_task(
                    user_id=self.user.id,
                    title='测试调研任务',
                    object_name='特斯拉',
                    object_type='COMPANY',
                    search_params={'keyword': '新能源'}
                )

        self.assertTrue(success)
        self.assertIsNone(error)
        self.assertIsNotNone(task_id)
        enqueue_mock.assert_called_once_with(task_id)

        task = ResearchTask.objects.get(pk=task_id)
        self.assertEqual(task.status, 'PENDING')
        self.assertEqual(task.dispatch_status, DISPATCH_PENDING)
        self.assertEqual(task.retry_count, 0)

        logs = list(TaskStepLog.objects.filter(task=task).order_by('sequence'))
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0].step_code, 'task_created')
        self.assertEqual(logs[0].step_status, 'COMPLETED')

    def test_runner_executes_pipeline_and_writes_ordered_logs(self):
        task = ResearchTask.objects.create(
            user=self.user,
            title='执行任务',
            object_name='苹果',
            object_type='COMPANY',
            status='PENDING',
            dispatch_status=DISPATCH_QUEUED,
            progress={'searching': 0, 'analyzing': 0, 'report': 0},
        )

        ResearchTaskRunner.run_task(task.id)

        task.refresh_from_db()
        self.assertEqual(task.status, STATUS_COMPLETED)
        self.assertEqual(task.dispatch_status, DISPATCH_FINISHED)
        self.assertIsNotNone(task.started_at)
        self.assertIsNotNone(task.finished_at)
        self.assertEqual(task.progress, {'searching': 100, 'analyzing': 100, 'report': 100})
        self.assertEqual(task.retry_count, 1)

        logs = list(TaskStepLog.objects.filter(task=task).order_by('sequence', 'created_at'))
        self.assertGreaterEqual(len(logs), 6)
        self.assertEqual(logs[0].step_code, 'dispatch_started')
        self.assertEqual(logs[-1].step_code, 'task_completed')
        self.assertTrue(all(log.sequence == index for index, log in enumerate(logs, start=1)))
        self.assertTrue(any(log.step_code == 'analysis_finish' for log in logs))

        status_view = get_task_status_view(task.id)
        self.assertEqual(status_view['status'], 'completed')
        self.assertEqual(status_view['dispatch_status'], 'finished')

        detail = get_task_detail(task.id)
        self.assertEqual(detail['dispatch_status'], DISPATCH_FINISHED)
        self.assertIsNotNone(detail['runner_token'])

        step_logs = get_task_step_logs(task.id)
        self.assertEqual(step_logs['nodes'][0]['step_code'], 'dispatch_started')
        self.assertEqual(step_logs['nodes'][-1]['step_code'], 'task_completed')

        events = get_task_events(task.id)
        self.assertEqual(events[-1]['node_status'], 'completed')

    def test_cancel_task_marks_dispatch_cancelled_and_appends_log(self):
        task = ResearchTask.objects.create(
            user=self.user,
            title='取消任务',
            object_name='英伟达',
            object_type='COMPANY',
            status='SEARCHING',
            dispatch_status='RUNNING',
            progress={'searching': 50, 'analyzing': 0, 'report': 0},
        )

        success, error = cancel_task(task.id, self.user.id)

        self.assertTrue(success)
        self.assertIsNone(error)
        task.refresh_from_db()
        self.assertEqual(task.status, STATUS_CANCELLED)
        self.assertEqual(task.dispatch_status, DISPATCH_CANCELLED)
        self.assertIsNotNone(task.finished_at)
        self.assertTrue(TaskStepLog.objects.filter(task=task, step_code='task_cancelled').exists())

    def test_runner_marks_failure_and_records_error_log(self):
        task = ResearchTask.objects.create(
            user=self.user,
            title='失败任务',
            object_name='异常公司',
            object_type='COMPANY',
            status='PENDING',
            dispatch_status=DISPATCH_QUEUED,
            progress={'searching': 0, 'analyzing': 0, 'report': 0},
        )

        with mock.patch.object(ResearchTaskRunner, '_run_pipeline', side_effect=RuntimeError('boom')):
            ResearchTaskRunner.run_task(task.id)

        task.refresh_from_db()
        self.assertEqual(task.status, STATUS_FAILED)
        self.assertEqual(task.dispatch_status, DISPATCH_FAILED)
        self.assertEqual(task.last_error, 'boom')
        self.assertTrue(TaskStepLog.objects.filter(task=task, step_code='task_failed').exists())

    def test_recover_incomplete_tasks_requeues_only_recoverable_tasks(self):
        recoverable = ResearchTask.objects.create(
            user=self.user,
            title='待恢复',
            object_name='阿里巴巴',
            object_type='COMPANY',
            status=STATUS_FAILED,
            dispatch_status=DISPATCH_FAILED,
            progress={'searching': 80, 'analyzing': 0, 'report': 0},
        )
        completed = ResearchTask.objects.create(
            user=self.user,
            title='已完成',
            object_name='腾讯',
            object_type='COMPANY',
            status=STATUS_COMPLETED,
            dispatch_status=DISPATCH_FINISHED,
            progress={'searching': 100, 'analyzing': 100, 'report': 100},
        )

        with mock.patch.object(ResearchTaskRunner, 'enqueue', side_effect=[True]) as enqueue_mock:
            recovered = recover_research_tasks()

        self.assertEqual(recovered, 1)
        enqueue_mock.assert_called_once_with(recoverable.id)
        completed.refresh_from_db()
        self.assertEqual(completed.dispatch_status, DISPATCH_FINISHED)
