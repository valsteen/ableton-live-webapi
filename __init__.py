try:
    import sys, os
    sys.path.append(os.path.join(os.path.dirname(__file__), "ext_libs"))

    from WebAPI import WebAPI
except ImportError:
    pass

def create_instance(c_instance):
    return WebAPI(c_instance)
