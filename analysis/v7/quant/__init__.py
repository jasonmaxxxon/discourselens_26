def run_pre_analyst(*args, **kwargs):
    from .facade import run_pre_analyst as _impl

    return _impl(*args, **kwargs)


def v7_payload_to_v6_structure(*args, **kwargs):
    from .facade import v7_payload_to_v6_structure as _impl

    return _impl(*args, **kwargs)


__all__ = ["run_pre_analyst", "v7_payload_to_v6_structure"]
