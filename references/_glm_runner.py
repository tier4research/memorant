import requests, json, os, sys

# Read the API key directly from Dropbox
key_path = r'F:\Dropbox\API keys\opencode\opencode go.txt'
if not os.path.exists(key_path):
    # Fallback: try environment variable
    key = os.environ.get('OPENCODE_GO_API_KEY', '')
    print(f'Key from env: {bool(key)}', flush=True)
else:
    with open(key_path, 'r') as f:
        key = f.read().strip()
    print(f'Key from file: {len(key)} chars', flush=True)

prompt_path = r'C:\Users\Admin\Documents\tier4-infra\memorant\references\_glm-doctrine-full.md'
out_path = r'C:\Users\Admin\Documents\tier4-infra\memorant\references\glm-doctrine-analysis.md'

print(f'Reading: {prompt_path}', flush=True)
print(f'Exists: {os.path.exists(prompt_path)}', flush=True)

with open(prompt_path, 'r', encoding='utf-8') as f:
    prompt = f.read()

print(f'Prompt: {len(prompt)} chars', flush=True)
key = os.environ.get('OPENCODE_GO_API_KEY', '')
print(f'Key present: {bool(key)}', flush=True)

resp = requests.post(
    'https://opencode.ai/zen/go/v1/chat/completions',
    headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'},
    json={'model': 'glm-5.2', 'messages': [{'role': 'user', 'content': prompt}], 'max_tokens': 8000},
    timeout=600
)

print(f'Status: {resp.status_code}', flush=True)
if resp.status_code == 200:
    result = resp.json()
    content = result['choices'][0]['message']['content']
    with open(out_path, 'w', encoding='utf-8') as out:
        out.write(content)
    print(f'Saved: {out_path} ({len(content)} chars)', flush=True)
    print(content[:600])
else:
    print(f'Error {resp.status_code}: {resp.text[:800]}', flush=True)
