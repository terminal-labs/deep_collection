import operator
from functools import reduce
from functools import wraps


def _stringlike(obj):
    """Return True if obj is an instance of str, bytes, or bytearray
    >>> _stringlike("a")
    True
    >>> _stringlike(b"a")
    True
    >>> _stringlike(1)
    False
    """
    return any(isinstance(obj, base) for base in (str, bytes, bytearray))


def _can_be_deep(obj):
    """Determine if an object could be a nested collection.

    The general heuristic is that an object must be iterable, but not stringlike
    so that an element can be arbitrary, like another collection.
    >>> _can_be_deep([1])
    True
    >>> _can_be_deep({1:2})
    True
    >>> _can_be_deep(1)
    False
    >>> _can_be_deep("a")
    False
    """
    try:
        iter(obj)
    except TypeError:
        return False

    if _stringlike(obj):
        return False

    return True


def del_by_path(obj, path):
    """Delete a key-value in a nested object in root by item sequence.
    from https://stackoverflow.com/a/14692747/913080

    >>> obj = {"a": ["b", {"c": "d"}]}
    >>> del_by_path(obj, ("a", 0))
    >>> obj
    {'a': [{'c': 'd'}]}
    """
    del get_by_path(obj, path[:-1])[path[-1]]


def get_by_path(obj, path):
    """Access a nested object in obj by iterable path.
    from https://stackoverflow.com/a/14692747/913080

    >>> obj = {"a": ["b", {"c": "d"}]}
    >>> get_by_path(obj, ["a", 1, "c"])
    'd'
    """
    return reduce(operator.getitem, path, obj)


def set_by_path(obj, path, value):
    """Set a value in a nested object in obj by iterable path.

    If the path doesn't fully exist, this will create it to set the value,
    making dicts along the way.

    >>> obj = {"a": ["b", {"c": "d"}]}
    >>> set_by_path(obj, ["a", 0], "foo")
    >>> obj
    {'a': ['foo', {'c': 'd'}]}
    >>> set_by_path(obj, ["a", 1, "e"], "foo")
    >>> obj
    {'a': ['foo', {'c': 'd', 'e': 'foo'}]}
    >>> obj = {}
    >>> set_by_path(obj, range(10), 10)
    >>> obj
    {0: {1: {2: {3: {4: {5: {6: {7: {8: {9: 10}}}}}}}}}}
    """
    path = list(path)  # needed for later equality checks

    traversed = []
    for part in path:
        branch = get_by_path(obj, traversed)
        traversed.append(part)

        try:
            branch[part]
        except (IndexError, KeyError):
            branch[part] = {}

        if traversed == path:
            branch[part] = value


def paths_to_field(obj, field, current=None):
    """Return the path to a specified field and the associated value. This is useful to
    determin what is using e.g. "password".

    Because this can handle a generalized config, which can have lists in it, this can
    also handle a list of all configs.

    >>> list(paths_to_field({"x": "value"}, "x"))
    [['x']]
    >>> list(paths_to_field({"x": {"y": "value"}}, "y"))
    [['x', 'y']]
    >>> list(paths_to_field(["value"], "x"))
    []
    >>> list(paths_to_field([{"x": "value"}], "x"))
    [[0, 'x']]
    >>> list(paths_to_field({"x": ["a", "b", {"y": "value"}]}, "y"))
    [['x', 2, 'y']]
    >>> list(paths_to_field([{"x": {"y": "value", "z": {"y": "asdf"}}}], "y"))
    [[0, 'x', 'y'], [0, 'x', 'z', 'y']]
    >>> # compound field
    >>> list(paths_to_field([{"x": {"y": "value", "z": {"y": "asdf"}}}], ["x", "y"]))
    [[0, 'x', 'y']]
    >>> list(paths_to_field({"x": {"y": "value"}}, ["y"]))
    [['x', 'y']]
    """
    if current is None:
        current = []

    if not _can_be_deep(obj):
        raise TypeError(
            f"First argument must be able to be deep, not type '{type(obj)}'"
        )

    # field is compound
    if _can_be_deep(field):
        try:
            get_by_path(obj, field)
        except (KeyError, IndexError, TypeError):
            try:
                for k, v in obj.items():
                    yield from paths_to_field(v, field, current + [k])
            except AttributeError:  # no .items
                for idx, i in enumerate(obj):
                    yield from paths_to_field(i, field, current + [idx])
        else:
            yield current + list(field)
    else:  # field is simple; str, int, float, etc.
        try:
            for k, v in obj.items():
                if k == field:
                    yield current + [k]
                if _can_be_deep(v):
                    yield from paths_to_field(v, field, current + [k])
        except AttributeError:  # no .items
            for idx, i in enumerate(obj):
                if i == field:
                    yield current + [idx]
                if _can_be_deep(i):
                    yield from paths_to_field(i, field, current + [idx])


def values_for_field(obj, field):
    """Generate all values for a given field.

    >>> list(values_for_field([{"x": {"y": "value", "z": {"y": "value"}}, "y": {1: 2}}], "y"))
    ['value', 'value', {1: 2}]
    """
    for path in paths_to_field(obj, field):
        yield get_by_path(obj, path)


def deduped_items(items):
    """Return a dedpued list of all items. This is not trivial
    since some values are dicts and thus are not hashable.

    >>> deduped_items(["a", {1:{2:{3:4}}}, {4}, {1:{2:{3:4}}}, {4}, {4}, 1, 1, "a"])
    [{1: {2: {3: 4}}}, {4}, 1, 'a']
    >>> deduped_items([{1:{2:{3:4}}}, {1:{2:{3:4}}}, {1:{2:{3:7}}}])
    [{1: {2: {3: 4}}}, {1: {2: {3: 7}}}]
    """

    try:
        return list(set(items))
    except TypeError:
        # next line from https://stackoverflow.com/a/9428041
        return [i for n, i in enumerate(items) if i not in items[n + 1 :]]  # noqa: E203


class DynamicSubclasser(type):
    """Return an instance of the class that uses this as its metaclass.
    This metaclass allows for a class to be instantiated with an argument,
    and assume that argument's class as its parent class.

    >>> class Foo(metaclass=DynamicSubclasser): pass
    >>> foo = Foo(5)
    >>> isinstance(foo, int)
    True
    """

    def __call__(cls, *args, **kwargs):
        if args:
            obj = args[0]
            args = args[1:]
        else:
            obj = {}

        dynamic_parent_cls = type(obj)

        # Make a new_cls that inherits from the dynamic_parent_cls, and resolving
        # potential metaclass conflicts if the dynamic_parent_cls doesn't already
        # use this metaclass.
        mcls = type(cls)
        if isinstance(dynamic_parent_cls, mcls):
            new_cls = type(cls.__name__, (dynamic_parent_cls,), {})
        else:
            cls.__class__ = type(mcls.__name__, (mcls, type(dynamic_parent_cls)), {})
            new_cls = type(cls.__name__, (cls, dynamic_parent_cls), {})

        # Create the instance and initialize it with the given object.
        # Sets obj in instance for immutables like `tuple`
        # instance = new_cls.__new__(new_cls, obj, *args, **kwargs)
        instance = new_cls.__new__(new_cls, obj, *args, **kwargs)
        # Sets obj in instance for mutables like `list`
        instance.__init__(obj, *args, **kwargs)

        return instance

    def __instancecheck__(cls, inst):
        """If the dynamic parent subclasses an abc (like UserList), the result
        __instancecheck__ would fail against the original base class.

        I think this is because our calculated class's __instancecheck__ would rely on
        ._abc_impl being implemented correctly on the immediate parent class, which is
        our base. Rather than trying to correctly implement a modified _abc_impl
        ourselves, we can use the naive method given in
        https://peps.python.org/pep-3119/#overloading-isinstance-and-issubclass
        """
        return any(cls.__subclasscheck__(c) for c in {type(inst), inst.__class__})

    def __subclasscheck__(cls, sub):
        """If the dynamic parent subclasses an abc (like UserList), the result
        __subclasscheck__ would fail against the original base class.

        I think this is because our calculated class's __subclasscheck__ would rely on
        ._abc_impl being implemented correctly on the immediate parent class, which is
        our base. Rather than trying to correctly implement a modified _abc_impl
        ourselves, we can use the naive method given in
        https://peps.python.org/pep-3119/#overloading-isinstance-and-issubclass
        """
        candidates = cls.__dict__.get("__subclass__", set()) | {cls}
        return any(c in candidates for c in sub.mro())


class DeepCollection(metaclass=DynamicSubclasser):
    """A class intended to allow easy access to items of deep collections.

    >>> dc = DeepCollection({"a": ["i", "j", "k"]})
    >>> dc["a", 1]
    'j'
    """

    def __init__(self, obj, *args, return_deep=True, **kwargs):
        # This often sets the original value for `self` for mutable types.
        # I.e. it gives a new list its content.
        # Immutables like tuples often already have the base class set via __new__.

        try:
            super().__init__(obj, *args, **kwargs)
        except TypeError:  # self is immutable - like a tuple
            pass
        except AttributeError as e:
            # NOTE: compat - dotty_dict
            # This amounts to a token compatibility with dotty_dict. Many methods and
            # features overlap. Try to prefer ours, and fall back to theirs.
            #
            # dotty_dict enforces an instance check on its first arg that dotty_dict
            # itself fails.
            #
            # Can't test for dotty without dotty available. If that's the case, fall
            # back to the original AttributeError because we can't tell why we get it.
            try:
                from dotty_dict import Dotty
            except ModuleNotFoundError:
                raise e

            if isinstance(obj, Dotty):
                super().__init__(obj.to_dict(), *args, **kwargs)
            else:  # We have Dotty available, but that's not obj.
                raise e

        # self._obj is never meant to be set except from self because we can't
        # easily update self if self._obj changes.
        self._obj = obj

        self.return_deep = return_deep

    def _ensure_post_call_sync(self, method):
        """Wrap any method to ensure that if if mutates self,
        self.obj is mutated to match. Inherited methods like list's `append` would act on
        self, but we also rely on composistion, so self.obj needs to be kept in sync.

        This decorator, used in __getattribute__, allows us to passively catch such
        cases without having to know ahead of time what these methods are. This gives
        us more generality, and also let's us not have to include such methods in this
        class. We also don't want an e.g. `append` here unless the parent has it.
        """

        @wraps(method)
        def wrapped(*args, **kwargs):
            result = method(*args, **kwargs)
            if self._obj != self:
                self._obj = type(self._obj)(self)
            return result

        return wrapped

    def __getattribute__(self, name):
        """Overridden to ensure self._obj stays in sync with self if self mutates
        from an inherited method being called.

        See self._ensure_post_call_sync
        """
        if name not in dir(DeepCollection) and name in dir(self):
            method = object.__getattribute__(self, name)
            if callable(method) and not name.startswith("_"):
                return self._ensure_post_call_sync(method)
        return object.__getattribute__(self, name)

    def __getattr__(self, item):
        """This turns a missing dot attr access into a getitem attempt."""
        try:
            return self[item]
        except (KeyError, IndexError, TypeError):
            raise AttributeError(f"'DeepCollection' object has no attribute '{item}'")

    def __getitem__(self, path):
        def get_raw():
            # Use self._obj instead of self to avoid unnecessary intermediate
            # DeepCollections. Just make a final conversion at the end.

            # Assume strs aren't supposed to be iterated through.
            if _stringlike(path):
                return self._obj[path]

            try:
                iter(path)
            except TypeError:
                return self._obj[path]

            return get_by_path(self._obj, path)

        rv = get_raw()

        if _can_be_deep(rv) and self.return_deep:
            return DeepCollection(rv)
        return rv

    def __delitem__(self, path):
        if _can_be_deep(path):
            del_by_path(self, path)
        else:
            super().__delitem__(path)
            del self._obj[path]

    def __setitem__(self, path, value):
        if _can_be_deep(path):
            set_by_path(self, path, value)
        else:
            super().__setitem__(path, value)
            self._obj[path] = value

    def __repr__(self):
        return f"DeepCollection({super().__repr__()})"

    def get(self, path, default=None):
        try:
            return self.__getitem__(path)
        except (KeyError, IndexError, TypeError):
            return default

    def paths_to_field(self, field):
        """
        >>> list(DeepCollection([{"x": {"y": "value", "z": {"y": "asdf"}}}]).paths_to_field("y"))
        [[0, 'x', 'y'], [0, 'x', 'z', 'y']]
        """
        yield from paths_to_field(self, field)

    def values_for_field(self, field):
        """
        >>> dc = DeepCollection([{"x": {"y": "v", "z": {"y": "v"}}, "y": {1: 2}}], return_deep=False)
        >>> list(dc.values_for_field("y"))
        ['v', 'v', {1: 2}]
        """
        yield from values_for_field(self, field)

    def deduped_values_for_field(self, field):
        """
        >>> dc = DeepCollection([{"x": {"y": "v", "z": {"y": "v"}}, "y": {1: 2}}], return_deep=False)
        >>> 'v' in dc.deduped_values_for_field("y")  # order not gaurunteed
        True
        >>> {1: 2} in dc.deduped_values_for_field("y")  # order not gaurunteed
        True
        """
        return deduped_items(list(values_for_field(self, field)))
