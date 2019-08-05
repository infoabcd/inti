from gevent.pywsgi import WSGIServer, WSGIHandler
from gevent import Timeout
class FDSafeHandler(WSGIHandler):
    '''Our WSGI handler. Doesn't do much non-default except timeouts'''
    def handle(self):
        self.timeout = Timeout(120, Exception)
        self.timeout.start()
        try:
            WSGIHandler.handle(self)
        except gevent.Timeout as ex:
            if ex is self.timeout:
                pass
            else: 
                raise
    '''
    def handle_error(self, two, three, four):
        if two is self.timeout:
            self.result = [b"Timeout"]
            self.start_response("200 OK", [])
            self.process_result()
        else:
            WSGIHandler.handle_error(self) 
    '''