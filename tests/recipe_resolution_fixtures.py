"""Pequena fixture de store público para os contratos do analisador."""

import json


def memory_snapshot(extra=None, prefix='', remove=(), repository_id=900000001, commit_sha='0' * 40):
    manifest = {'name': 'fixture', 'version': '1.0.0', 'scripts': {'test': 'vitest run'}}
    lock = {'name': 'fixture', 'version': '1.0.0', 'lockfileVersion': 3,
            'packages': {'': {'name': 'fixture', 'version': '1.0.0'},
                         'node_modules/vitest': {'version': '4.1.10'},
                         'node_modules/vite': {'version': '7.3.6'}}}
    files = {'package.json': json.dumps(manifest), 'package-lock.json': json.dumps(lock),
             'README.md': '## Qualidade\n```sh\nnpm test\nnpm run lint\n```\n',
             'src/example.test.ts': 'test("example", () => {});'}
    if prefix:
        files = {prefix + '/' + path: value for path, value in files.items()}
    files.update(extra or {})
    for path in remove:
        files.pop(path, None)
    files = {path: value.encode() if isinstance(value, str) else value for path, value in files.items()}
    reference = {'format': 'git-bare-v1', 'cache_key': f'{repository_id}/{commit_sha}',
                 'repository_id': repository_id, 'commit_sha': commit_sha,
                 'tree_oid': 'a' * 40, 'manifest_sha256': 'b' * 64,
                 'file_count': len(files), 'total_blob_bytes': sum(map(len, files.values())),
                 'status': 'complete'}

    class Store:
        closed = False

        def open_verified(self, reference):
            return self

        def reference(self):
            return dict(reference)

        def list_entries(self, artifact):
            return [{'path': path, 'type': 'blob', 'mode': '100644', 'size': len(value)}
                    for path, value in files.items()]

        def open_reader(self, artifact):
            return self

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.closed = True

        def read_blobs(self, paths):
            return {path: files[path] for path in paths}

    return {'context': {'snapshot': dict(reference)}}, Store()
