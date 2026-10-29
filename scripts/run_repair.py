"""Submit a fixture through the durable API; no dotenv or process-runner fallback."""
import argparse
import json
import os
from pathlib import Path
import time
import uuid
import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('fixture', help='Sample name or fixtures/repos/<sample>')
    parser.add_argument('--mode', choices=['full', 'generic'], default='full')
    parser.add_argument('--out', required=True)
    parser.add_argument('--api', default='http://127.0.0.1:8000')
    parser.add_argument('--timeout', type=int, default=1800)
    args = parser.parse_args()
    name = Path(args.fixture).name
    headers = {'X-Autopilot-Session': uuid.uuid4().hex}
    if token := os.environ.get('AUTOPILOT_CLI_TOKEN'):
        headers['X-Autopilot-Token'] = token
        if workspace := os.environ.get('AUTOPILOT_CLI_WORKSPACE'):
            headers['X-Workspace'] = workspace
    with httpx.Client(base_url=args.api.rstrip('/') + '/api/v1/v5/', headers=headers, timeout=60) as client:
        def request(method, path, **kwargs):
            response = client.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json()
        catalog = request('GET', 'samples')['samples']
        sample = next((s for s in catalog if s['name'] == name), None)
        if sample is None:
            parser.error('unknown bundled sample; inspect /api/v1/v5/samples')
        staged = request('POST', 'repos', json={'sample': name})
        task = request('POST', 'tasks', json={'repo_id': staged['id'], 'mode': args.mode,
                       'target': sample['task'], 'invite_code': os.environ.get('AUTOPILOT_CLI_INVITE')})
        end, after = time.monotonic() + args.timeout, 0
        terminal = {'REVIEW_READY', 'PR_DRAFTED', 'REJECTED', 'INCONCLUSIVE', 'FAILED', 'CANCELLED', 'BLOCKED'}
        while time.monotonic() < end:
            for event in request('GET', f"tasks/{task['id']}/events", params={'after': after}):
                print(f"[{event['seq']}] {event['type']}", flush=True)
                after = max(after, event['seq'])
            task = request('GET', f"tasks/{task['id']}")
            if task['status'] in terminal:
                response = client.get(f"tasks/{task['id']}/report")
                if response.status_code == 409 and task['status'] in {'FAILED', 'CANCELLED', 'BLOCKED'}:
                    report = {'task_id': task['id'], 'status': task['status'], 'error': task.get('error'), 'decision': 'INCONCLUSIVE'}
                else:
                    response.raise_for_status()
                    report = response.json()
                out = Path(args.out)
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(json.dumps(report, indent=2), encoding='utf-8')
                print(json.dumps({'task_id': task['id'], 'status': task['status'], 'decision': report['decision']}))
                return 0
            time.sleep(2)
        raise TimeoutError(f"Task {task['id']} is still running; inspect it in the UI. It was not cancelled.")


if __name__ == '__main__':
    raise SystemExit(main())
