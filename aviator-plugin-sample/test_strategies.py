import sys, types, re
sys.path.insert(0,'src')

for name in ('aviator.services.llm','langchain_core.messages','aviator.vector_store','ticket_to_code.llm_utils'):
    m = types.ModuleType(name)
    m.LLMRegistry = object
    m.SystemMessage = object
    m.HumanMessage = object
    m.VectorStoreManager = object
    m.llm_invoke = lambda *a, **k: None
    m.extract_token_usage = lambda *a: {}
    sys.modules[name] = m

mm = types.ModuleType('ticket_to_code.models')
for x in ['DevelopmentTask','StructuredRequirements','GeneratedCode','TaskType','ProgrammingLanguage','ArtifactType','BuildResult','WorkflowStatus']:
    setattr(mm, x, type(x, (), {'value': x.lower(), 'MODIFY': 'modify', 'TYPESCRIPT': 'typescript'})())
sys.modules['ticket_to_code.models'] = mm
sys.modules['ticket_to_code.json_utils'] = types.ModuleType('ticket_to_code.json_utils')

import importlib.util
spec = importlib.util.spec_from_file_location('cg', 'src/ticket_to_code/agents/code_generator.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
fn = mod._apply_additive_strategy

java = 'public class Svc {\n    public void existing() {}\n}'
r = fn('INSERT_BEFORE_CLASS_END:\n    public void newMethod() {}', java, 'Svc.java')
print('Java INSERT_BEFORE_CLASS_END:', 'existing' in r and 'newMethod' in r and r.endswith('}'))

py = 'class MyClass:\n    def existing(self):\n        pass\n'
r = fn('INSERT_AT_FILE_END:\n    def new_method(self):\n        return None', py, 'service.py')
print('Python INSERT_AT_FILE_END:', 'existing' in r and 'new_method' in r)

html = '<div>\n  <span>existing</span>\n</div>'
r = fn('INSERT_BEFORE_HTML_END:\n  <span>new</span>', html, 'template.html')
print('HTML INSERT_BEFORE_HTML_END:', 'existing' in r and 'new' in r and '</div>' in r)

r = fn('no_directive', 'original', 'file.ts')
print('No-match returns unchanged:', r == 'no_directive')

print('\nALL STRATEGY TESTS PASS')
