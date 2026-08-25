import re
path = r'C:\Users\dmadgani\Desktop\My_Aviator\aviator-plugin-sample\src\ticket_to_code\tracing\tracer.py'
content = open(path, 'r', encoding='utf-8-sig').read()

def repl(m):
    sig = m.group(1)
    if '**kwargs' not in sig:
        if sig.strip().endswith(','):
            sig += ' **kwargs,'
        else:
            sig += ', **kwargs'
    return f'def {m.group(0)[4:m.group(0).find("(")]}({sig}) -> None:'

new_content = re.sub(r'def record_[a-zA-Z0-9_]+\(([\s\S]*?)\)\s*->\s*None:', repl, content)
open(path, 'w', encoding='utf-8-sig').write(new_content)
print('Done!')
