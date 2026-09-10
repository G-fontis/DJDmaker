"""Conservative recovery of Notebook references; never bind by title alone."""
from urllib.parse import urlparse
from uuid import UUID


def notebook_identity(notebook_id=None, notebook_url=None):
    if notebook_url:
        parsed = urlparse(notebook_url)
        parts = parsed.path.strip('/').split('/')
        if parsed.scheme != 'https' or parsed.hostname != 'notebook.google.com' or len(parts) != 2 or parts[0] != 'notebook':
            raise ValueError('NOTEBOOK_IDENTITY_INVALID')
        identity = str(UUID(parts[1]))
        if notebook_id and notebook_id != identity:
            raise ValueError('NOTEBOOK_IDENTITY_MISMATCH')
    elif notebook_id:
        identity = str(UUID(notebook_id))
    else:
        return None
    return identity, 'https://notebook.google.com/notebook/' + identity


def recover_unique_identity(job, candidates):
    """Candidates are independently verified source identities, not search hits.

    Require both normalized source path and a known matching digest. An absent
    digest or two distinct remote IDs is insufficient evidence for auto-binding.
    """
    from .output_ownership import path_identity
    pairs = set()
    for candidate in candidates:
        if not job.source_sha256 or candidate.source_sha256 != job.source_sha256:
            continue
        if path_identity(candidate.source_path) != path_identity(job.source_path):
            continue
        pair = notebook_identity(candidate.notebook_id, candidate.notebook_url)
        if pair:
            pairs.add(pair)
    if len(pairs) != 1:
        return False
    job.notebook_id, job.notebook_url = pairs.pop()
    return True
