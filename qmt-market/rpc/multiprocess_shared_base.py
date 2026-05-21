#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
    multiprocess_shared_base.py

    Class to hold value for multiprocessing
    :copyright: (c) 2025 by nkwsqyyzx@gmail.com
    :license: BSD, see LICENSE for more details.
"""
from multiprocessing import Lock


class MPShared(object):
    def __init__(self):
        self.lock = Lock()  # 全局锁
        self.kvs = {}  # 键值对

    def keys(self):
        with self.lock:
            return list(self.kvs.keys())

    def set(self, key, value):
        with self.lock:
            self.kvs[key] = value

    def get(self, key, default=None):
        with self.lock:
            return self.kvs.get(key, default)

    def delete(self, key):
        with self.lock:
            return self.kvs.pop(key, None)

    def clear(self):
        with self.lock:
            old = self.kvs
            self.kvs = {}
            return old

    def hset(self, key, field, value):
        with self.lock:
            dict_val = self.kvs.setdefault(key, {})
            dict_val[field] = value

    def hget(self, key, field, default=None):
        with self.lock:
            dict_val = self.kvs.setdefault(key, {})
            return dict_val.get(field, default)

    def rpush(self, key, value):
        with self.lock:
            queue = self.kvs.setdefault(key, [])
            queue.append(value)

    def rpop(self, key, default=None):
        with self.lock:
            queue = self.kvs.setdefault(key, [])
            if queue:
                return queue.pop()
            return default

    def sadd(self, key, value):
        with self.lock:
            the_set = self.kvs.setdefault(key, set())
            if value not in the_set:
                the_set.add(value)
                return 1
            return 0

    def srem(self, key, value):
        with self.lock:
            the_set = self.kvs.setdefault(key, set())
            if value in the_set:
                the_set.remove(value)
                return 1
            return 0

    def smembers(self, key):
        with self.lock:
            the_set = self.kvs.setdefault(key, set())
            return list(the_set)
