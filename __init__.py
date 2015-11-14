import sys, os, logging

logger = logging.getLogger("WebAPI")
if not hasattr(logger, "initialized"):
    # Live reloads the module, which is bad. Ensure we don't register the logger again and again
    logfile = logging.FileHandler(os.path.join(os.path.expanduser('~'), 'abletonwebapi.log'))
    logfile.setLevel(logging.DEBUG)

    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    logfile.setFormatter(formatter)

    logger.setLevel(logging.DEBUG)
    logger.addHandler(logfile)

    logger.initialized = True


try:
    sys.path.append(os.path.join(os.path.dirname(__file__), "ext_libs"))
    from WebAPI import WebAPI
except Exception, e:
    logger.exception()


def create_instance(c_instance):
    try:
        from WebAPI import WebAPI

        return WebAPI(c_instance)
    except Exception, e:
        logger.exception()