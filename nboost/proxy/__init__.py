from ..base import StatefulBase
from ..codex import BaseCodex
from ..model import BaseModel
from ..server import BaseServer
from ..db import BaseDb
from ..base.types import *
from typing import Type, Tuple, Dict, List, Any
from inspect import isawaitable
import json as JSON
import time


class Proxy(StatefulBase):
    def __init__(self,
                 host: str = '127.0.0.1',
                 port: int = 53001,
                 ext_host: str = '127.0.0.1',
                 ext_port: int = 54001,
                 lr: float = 10e-3,
                 data_dir: str = '/.cache',
                 multiplier: int = 10,
                 field: str = None,
                 server: Type[BaseServer] = BaseServer,
                 model: Type[BaseModel] = BaseModel,
                 codex: Type[BaseCodex] = BaseCodex,
                 db: Type[BaseDb] = BaseDb, **kwargs):
        """The proxy object is the core of nboost.It has four components:
        the model, server, db, and codex.The role of the proxy is to
        construct each component and create the route callback(search,
        train, status, not_found, and error).

        Each route callback is assigned a url path by the codex and then
        handed to the server to execute when it receives the respective url
        path request.The following __init__ contains the main executed
        functions in nboost.

        :param host: virtual host of the server.
        :param port: server port.
        :param ext_host: host of the external search api.
        :param ext_port: search api port.
        :param lr: learning rate of the model.
        :param data_dir: data directory to cache the model.
        :param multiplier: the factor to multiply the search request by. For
            example, in the case of Elasticsearch if the client requests 10
            results and the multiplier is 6, then the model should receive 60
            results to rank and refine down to 10 (better) results.
        :param field: a tag for the field in the search api result that the
            model should rank results by.
        :param server: uninitialized server class
        :param model: uninitialized model class
        :param codex: uninitialized codex class
        :param db: uninitialized db class
        """

        super().__init__(**kwargs)

        # pass command line arguments to instantiate each component
        server = server(host=host, port=port,
                        ext_host=ext_host, ext_port=ext_port)
        model = model(lr=lr, data_dir=data_dir)
        codex = codex(multiplier=multiplier, field=field)
        db = db()

        def track(f: Any):
            """Tags and times each component for benchmarking purposes. The
            data generated by track() is sent to the db who decides what to do
            with it (e.g. log, add to /status, etc...)"""

            if hasattr(f, '__self__'):
                cls = f.__self__.__class__.__name__
            else:
                cls = self.__class__.__name__
            # ident is the name of the object containing f() or the Proxy
            ident = (cls, f.__name__)

            async def decorator(*args):
                start = time.perf_counter()
                res = f(*args)
                ret = await res if isawaitable(res) else res
                ms = (time.perf_counter() - start) * 1000
                db.lap(ms, *ident)
                return ret
            return decorator

        @track
        async def search(_1: Request) -> Response:
            """The role of the search route is to take a request from the
            client, balloon it by the multipler and ask for that larger request
            from the search api, then filter the larger results with the
            model to return better results. """

            # Codex alters the request to make a larger one.
            _2: Request = await track(codex.magnify)(_1)

            # The server asks the search api for the larger request.
            _3: Response = await track(server.ask)(_2)

            # The codex takes the large response and parses out the query
            # from the amplified request and response.
            _4: Tuple[Query, Choices] = await track(codex.parse)(_2, _3)

            # the model ranks the choices based on the query.
            _5: Ranks = await track(model.rank)(*_4)

            # the db saves the query and choices, and returns the query id
            # and choice ids for the client to send back during train()
            _6: Tuple[Qid, List[Cid]] = await track(db.save)(*_4)

            # the codex formats the new (nboosted) response with the context
            # from the entire search pipeline.
            _7: Response = await track(codex.pack)(_1, _3, *_4, _5, *_6)
            return _7

        @track
        async def train(_1: Request) -> Response:
            """The role of the train route is to receive a query id and choice
            id from the client and train the model to choose that one next
            time for lack of better words."""

            # Parse out the query id and choice id(s) from the client request.
            _2: Tuple[Qid, List[Cid]] = await track(codex.pluck)(_1)

            # Db retrieves the content it saved during search(). It also
            # assigns a label to each choice based on the clients request.
            _3: Tuple[Query, Choices, Labels] = await track(db.get)(*_2)
            await track(model.train)(*_3)

            # acknowledge that the request was sent to the model
            _4: Response = await track(codex.ack)(*_2)
            return _4

        @track
        async def status(_1: Request) -> Response:
            """Status() chains the state from each component in order to
            return a formatted dictionary for /status"""
            _2: Dict = server.chain_state({})
            _3: Dict = codex.chain_state(_2)
            _4: Dict = model.chain_state(_3)
            _5: Dict = db.chain_state(_4)
            _6: Response = codex.pulse(_5)
            return _6

        @track
        async def not_found(_1: Request) -> Response:
            """What to do when none of the paths given to the server match
            the path requested by the client."""
            _2: Response = await track(server.forward)(_1)
            return _2

        @track
        async def error(_1: Exception) -> Response:
            """Errors during any route"""
            _2: Response = await track(codex.catch)(_1)
            return _2

        # create functional routes for the server
        routes = {
            Route.SEARCH: (codex.SEARCH, search),
            Route.TRAIN: (codex.TRAIN, train),
            Route.STATUS: (codex.STATUS, status),
            Route.ERROR: (codex.ERROR, error)
        }
        server.create_app(routes)

        self.is_ready = server.is_ready
        self.logger.info(JSON.dumps(dict(
            server=server.__class__.__name__,
            codex=codex.__class__.__name__,
            model=model.__class__.__name__,
            db=db.__class__.__name__,
        ), indent=4))

        self.server = server

    def enter(self):
        self.server.start()

    def exit(self):
        self.logger.critical('Stopping proxy...')
        self.server.exit()
        self.server.join()

    def __enter__(self):
        self.enter()
        self.is_ready.wait()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.exit()

