import sys

def fix_file(f, replacements):
    with open(f, 'r', encoding='utf-8') as file:
        content = file.read()
    for o, n in replacements:
        content = content.replace(o, n)
    with open(f, 'w', encoding='utf-8') as file:
        file.write(content)

fix_file('main.py', [
    ('kravein = KraveinLive(', 'kravein = KraveinLive('),
    ('kravein.plugin_manager', 'kravein.plugin_manager'),
    ('plugin_manager.register_kravein(kravein)', 'plugin_manager.register_kravein(kravein)'),
    ('plugin_manager.dispatch("on_startup", kravein)', 'plugin_manager.dispatch("on_startup", kravein)'),
    ('asyncio.run(kravein.run())', 'asyncio.run(kravein.run())')
])

fix_file('plugin_manager.py', [
    ('self.kravein', 'self.kravein'),
    ('kravein=self.kravein', 'kravein=self.kravein')
])

print('Fixes applied!')
