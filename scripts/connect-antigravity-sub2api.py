#!/usr/bin/env python3
"""Bind an Antigravity API Key account to a standard multi-model Sub2API group."""

import argparse
import json
import pathlib
import sys
import urllib.error
import urllib.request


def connect(request, api_key, upstream_url, models, group_name='antigravity', account_name='antigravity-lxc158'):
    models = sorted(set(models))
    if not api_key:
        raise ValueError('An upstream API key is required')
    groups = request('GET', '/admin/groups/all')
    group = next((value for value in groups if value['name'] == group_name), None)
    settings = {'name': group_name, 'platform': 'openai', 'description': 'Antigravity Manager multi-model OpenAI API',
                'rate_multiplier': 1, 'is_exclusive': False, 'subscription_type': 'standard',
                'model_allowlist': {'enabled': False}}
    if group:
        if group['platform'] != 'openai':
            raise ValueError('The existing group has a different platform')
        group = request('PUT', f"/admin/groups/{group['id']}", {'model_allowlist': {'enabled': False}, 'status': 'active'})
    else:
        group = request('POST', '/admin/groups', settings)

    response = request('GET', '/admin/accounts?page=1&page_size=500')
    accounts = response.get('items', []) if isinstance(response, dict) else response
    account = next((value for value in accounts if value['name'] == account_name), None)
    credentials = {'api_key': api_key, 'base_url': upstream_url.rstrip('/'),
                   'model_mapping': {model: model for model in models}}
    extra = {'openai_passthrough': True, 'openai_passthrough_model_filter': False,
             'openai_responses_mode': 'auto', 'upstream_billing_probe_enabled': False}
    if account:
        if account['platform'] != 'openai' or account['credentials'].get('base_url', '').rstrip('/') != upstream_url.rstrip('/'):
            raise ValueError('The existing account points to a different upstream')
        account = request('PUT', f"/admin/accounts/{account['id']}",
                          {'credentials': credentials, 'extra': extra, 'group_ids': [group['id']], 'status': 'active'})
    else:
        account = request('POST', '/admin/accounts', {'name': account_name, 'platform': 'openai', 'type': 'apikey',
                          'credentials': credentials, 'extra': extra, 'concurrency': 2, 'priority': 1,
                          'group_ids': [group['id']]})
    # Robot keys are deliberately not part of this operation.
    return {'group_id': group['id'], 'group_name': group_name, 'account_id': account['id'],
            'account_name': account_name, 'model_count': len(models), 'model_allowlist_enabled': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sub2api-url', default='http://127.0.0.1:8080')
    parser.add_argument('--admin-token-file', required=True, type=pathlib.Path)
    parser.add_argument('--env-file', type=pathlib.Path, default=pathlib.Path('runtime/stack.env'))
    parser.add_argument('--upstream-url', default='http://antigravity:8045')
    parser.add_argument('--models-url', default='http://127.0.0.1:8045/v1/models')
    parser.add_argument('--manual-accounts', action='store_true',
                        help='Allow routing setup while the WebUI has no accounts and its proxy returns 503')
    args = parser.parse_args()
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'lib'))
    import proxykit
    values = proxykit.read_env(args.env_file)
    token = args.admin_token_file.read_text().strip()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(method, path, payload=None):
        body = json.dumps(payload).encode() if payload is not None else None
        headers = {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'}
        with opener.open(urllib.request.Request(args.sub2api_url.rstrip('/') + '/api/v1' + path,
                         data=body, method=method, headers=headers), timeout=30) as response:
            data = json.load(response)
        return data.get('data', data)

    try:
        with opener.open(urllib.request.Request(args.models_url,
                         headers={'Authorization': 'Bearer ' + values['ANTIGRAVITY_API_KEY']}), timeout=30) as response:
            models = [value['id'] for value in json.load(response)['data']]
    except urllib.error.HTTPError as error:
        if not args.manual_accounts or error.code != 503:
            raise
        models = []
    print(json.dumps(connect(request, values['ANTIGRAVITY_API_KEY'], args.upstream_url, models)))


if __name__ == '__main__':
    main()
