with open(r'C:\Users\dmadgani\Desktop\My_Aviator\aviator-plugin-sample\chatbot\backend\main.py', encoding='utf-8') as f:
    lines = f.readlines()

# Show repr of lines 878-892
for i in range(877, 892):
    print(f'{i+1:4}: {repr(lines[i][:80])}')

# Count all """ in file, track state
print('\n--- Triple-quote tracking (last 20 occurrences) ---')
in_string = False
events = []
for i, line in enumerate(lines, 1):
    if '"""' in line:
        count = line.count('"""')
        prev_state = in_string
        if count % 2 == 1:
            in_string = not in_string
        events.append((i, count, prev_state, in_string, line.rstrip()[:70]))
for e in events[-20:]:
    print(f'{e[0]:4} count={e[1]} {e[2]}->{e[3]}: {e[4]}')
print(f'Final in_string={in_string}')
