import re

with open(r'atlas\api\routes.py', encoding='utf-8') as f:
    text = f.read()

for m in re.finditer(r'@api_bp\.route\(["\']([^"\']+)["\'].*?\)\s*(?:#[^\n]*\s*)*def\s+([a-zA-Z0-9_]+)', text):
    print(f"{m.group(1)} -> {m.group(2)}")
