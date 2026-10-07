from agent.prompts import prompt
from agent.tasks.service import control_worker, message_task_thread, spawn_worker, task_status

for _tool in (spawn_worker, task_status, message_task_thread, control_worker):
    _tool.__doc__ = prompt(f"tasks/tool_{_tool.__name__}")
