import json
try:
    with open('history.json', encoding='utf-8') as f:
        data = json.load(f)
        tasks = data.get('tasks', {})
        if not tasks:
            print("No tasks found.")
        else:
            t = list(tasks.keys())[-1]
            task = tasks[t]
            print(f"Status: {task.get('status')}")
            print(f"Title: {task.get('title')[:100]}...")
except Exception as e:
    print(f"Error: {e}")
