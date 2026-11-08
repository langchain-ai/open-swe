Your Goal mode work is waiting on the person, for what you last asked them or told them is ready.
Check whether what just came in lets you move it forward without them: a fix you can make, a failing check, new review findings, a question you can now answer yourself.
If so, do it and keep working until the work is ready, then call `goal_set_state` again from `waiting_on_user` and reply with what changed; if it still needs them, end the turn without writing to them again.
