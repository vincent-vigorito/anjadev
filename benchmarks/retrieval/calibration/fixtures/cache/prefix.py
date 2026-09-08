def namespace_prefix(namespace):
    """Format a namespace, independently of tenant isolation keys."""
    return namespace.strip() + "/"
