# ADR 3: tenant isolation
Partition keys by tenant to prevent cross customer cache collisions.
Implemented by: storage.py:cache_key
