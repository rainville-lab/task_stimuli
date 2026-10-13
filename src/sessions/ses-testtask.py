from src.tasks.test_task import TestTask

def get_tasks(parsed):
    """Returns a list containing our minimal test task instance."""
    return [
        TestTask(name="testtask")
    ]