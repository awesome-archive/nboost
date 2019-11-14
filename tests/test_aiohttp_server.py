from nboost.server.aio import AioHttpServer
from nboost.base.types import *
import json as JSON
import requests
import unittest


class TestAioHttpServer(unittest.TestCase):
    def test_aiohttp_server(self):

        server = AioHttpServer(port=6000, ext_port=5900, verbose=True)

        async def get_stuff(req):
            return Response(b'HTTP/1.1', 200, {}, JSON.dumps(dict(heres='stuff')).encode())

        async def send_stuff(req):
            return Response(b'HTTP/1.1', 200, {}, b'I got ' + req.body)

        server.create_app([
            (b'/get_stuff', [b'GET'], get_stuff),
            (b'/send_stuff', [b'POST'], send_stuff),
        ], not_found_handler=lambda x: print(x))

        server.start()
        server.is_ready.wait()
        self.assertTrue(server.is_ready.is_set())

        res = requests.get('http://localhost:6000/get_stuff')
        self.assertTrue(res.ok)

        res = requests.post('http://localhost:6000/send_stuff', data=b'an avocado')
        self.assertTrue(res.ok)

        server.stop()
        server.join()
