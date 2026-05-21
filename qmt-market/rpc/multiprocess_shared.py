#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
    multiprocess_shared.py

    Class to hold value for multiprocessing
    :copyright: (c) 2025 by nkwsqyyzx@gmail.com
    :license: BSD, see LICENSE for more details.
"""
import time
from multiprocessing.managers import BaseManager

from .multiprocess_shared_base import MPShared

__the_instance = MPShared()


def __get_the_shared_instance():
    return __the_instance


# noinspection PyUnresolvedReferences
def start_shared_server(port=50000):
    # 启动服务
    BaseManager.register('get_shared', callable=__get_the_shared_instance)
    manager = BaseManager(address=('127.0.0.1', port), authkey=b'JUg5wB8KqMDB2x6u')
    manager.start()
    print("Manager server is running...")
    try:
        inst = manager.get_shared()
        while not inst.get('_@should_stop', False):
            time.sleep(60)
        manager.shutdown()
        print("Manager server quit...")
    except KeyboardInterrupt:
        manager.shutdown()


def start_shared_server_bg_thread(port=50000):
    import threading

    threading.Thread(target=start_shared_server, args=(port,)).start()


async def start_shared_server_async(port=50000):
    import asyncio
    await asyncio.to_thread(start_shared_server, port)


__fetch_kv_instance = {}


# noinspection PyUnresolvedReferences
def get_global_mp_shared(host, port=50000) -> MPShared:
    key = f'{host}:{port}'
    if key in __fetch_kv_instance:
        return __fetch_kv_instance[key]['inst']
    BaseManager.register('get_shared')
    manager = BaseManager(address=(host, port), authkey=b'JUg5wB8KqMDB2x6u')
    manager.connect()
    inst = manager.get_shared()
    __fetch_kv_instance[key] = {'inst': inst, 'manager': manager}
    return inst


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        start_shared_server(int(sys.argv[1]))
    else:
        # start_shared_server_bg_thread()
        # while True:
        #     time.sleep(1)
        import asyncio

        asyncio.run(start_shared_server_async())
