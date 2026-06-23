import sys
sys.path.insert(0, '/opt/data')

ok = 0
fail = 0

def check(name, condition, detail=''):
    global ok, fail
    if condition:
        print(f'PASS: {name}')
        ok += 1
    else:
        print(f'FAIL: {name} - {detail}')
        fail += 1

print('=== SSRF / Webcrawl Safety Tests ===')
print()

from webcrawl_lite import validate_url, is_allowed_content_type, verify_token, WebcrawlConfig

safe, reason = validate_url('https://example.com')
check('HTTPS URL allowed', safe, reason)

safe, reason = validate_url('http://example.com')
check('HTTP URL blocked', not safe, reason)

safe, reason = validate_url('https://127.0.0.1')
check('127.0.0.1 blocked', not safe, reason)

safe, reason = validate_url('https://10.0.0.1')
check('10.0.0.1 blocked', not safe, reason)

safe, reason = validate_url('https://192.168.1.1')
check('192.168.1.1 blocked', not safe, reason)

safe, reason = validate_url('https://169.254.169.254/latest/meta-data')
check('169.254.169.254 blocked', not safe, reason)

safe, reason = validate_url('https://[::1]')
check('IPv6 ::1 blocked', not safe, reason)

safe, reason = validate_url('not-a-url')
check('Invalid URL rejected', not safe, reason)

safe, reason = validate_url('ftp://example.com')
check('FTP scheme blocked', not safe, reason)

check('text/html allowed', is_allowed_content_type('text/html'))
check('application/json allowed', is_allowed_content_type('application/json'))
check('application/octet-stream blocked', not is_allowed_content_type('application/octet-stream'))

cfg = WebcrawlConfig(service_token='test-123', require_token=True)
check('Valid token accepted', verify_token('Bearer test-123', cfg))
check('Invalid token rejected', not verify_token('Bearer wrong', cfg))
check('No token rejected', not verify_token(None, cfg))

cfg2 = WebcrawlConfig(require_token=False)
check('No-auth accepts any', verify_token(None, cfg2))

print()
print(f'=== {ok} PASSED, {fail} FAILED ===')
sys.exit(0 if fail == 0 else 1)
