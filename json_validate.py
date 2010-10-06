import re
import functools
import operator
import logging

class JSONException(Exception):
    pass

# Matches dates like
# 1997-07-16T19:20:30
# or
# 1997-07-16T19:20:30.45
# or
# 1997-07-16T19:20:30+01:00
# or
# 1997-07-16T19:20:30.45+01:00
#
# You can use this like:
# @json_validate({'when_it_happened': json_timestamp})
json_timestamp = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?P<ms>\.\d*)?(?P<tz>\s?\+\d{2}:\d{2})?')

timestamp = int

def assert_json(bvalue, msg):
    """
    Throw a JSONException if bvalue is False.
    @param bvalue:  A bool
    @param msg:     Description of the error
    """
    if not bvalue: raise JSONException(msg)

def assert_json_type(t, value, path=''):
    """
    Throw JSONException if value is not of type t, or t is scalar type
    and value is None
    @param t:       A type like list, dict, str, int
    @param value:   The client value to test
    @param path:    Optional string describing the path from the root of
                    the JSON structure to the current node (supports
                    recursion)
    >>> assert_json_type(int, 1)
    >>> assert_json_type(float, 1)
    >>> assert_json_type(int, 1.0)
    >>> assert_json_type(float, 1.0)
    >>> assert_json_type(float, 'a')
    Traceback (most recent call last):
        ...
    JSONException: Type error:  = 'a', which is of type str.  A value of type float is required
    >>> assert_json_type(list, 'a')
    Traceback (most recent call last):
        ...
    JSONException: Type error:  = 'a', which is of type str.  A value of type list is required
    >>> assert_json_type(list, [])
    >>> assert_json_type(list, ())
    Traceback (most recent call last):
        ...
    JSONException: Type error:  = (), which is of type tuple.  A value of type list is required
    >>> assert_json_type(dict, {})
    >>> assert_json_type(dict, None)
    Traceback (most recent call last):
        ...
    JSONException: Type error:  = None, which is of type NoneType.  A value of type dict is required
    >>> assert_json_type(1, None)
    """
    # Special case: 3.0 is acceptable as an int
    if t is int and isinstance(value, float) and int(value) == value:
        return
    
    # Special case: ints acceptable as floats
    elif t is float and isinstance(value, int):
        return
    
    # Special case: str and unicode interchangeable
    elif t in (str, unicode) and (
        isinstance(value, str) or isinstance(value, unicode)
    ):
        return
    
    # Design decision: allow None for all scalar types
    if value is None and t not in (dict, list, one_of, required):
        return
    
    elif not isinstance(value, t):
        raise JSONException(
            'Type error: %s = %s, which is of type %s.  A value of type %s is required' % (
                path, repr(value), type(value).__name__, t.__name__
            )
        )


"""
A portion of a JSON structure which json_validate will validate as long as
it is present, regardless of its type or structure.
"""
class anytype: pass

class optional:
    """
    A portion of a JSON structure which json_validate considers optional.
    (But if the optional portion exists, it must validate.)
    >>> class C:
    ...     @json_validate({'foo':int, 'bar':optional(int)})
    ...     def fn(self, input):
    ...         print input
    ... 
    >>> c = C()
    >>> c.fn({'foo':1,'bar':1})
    {'foo': 1, 'bar': 1}
    >>> c.fn({'foo':1,'bar':'str'})
    Traceback (most recent call last):
        ...
    json_validate.JSONException: Type error: client_json['bar'] = 'str', which is of type str.  A value of type int is required
    >>> c.fn({'foo':1})
    {'foo': 1}
    """
    def __init__(self, value):
        self.value = value
    
    def __repr__(self):
        return "optional(%s)" % self.value


class json_validator_wrapper:
    """
    base class for validating dictionary-like JSON objects
    """
    def __init__(self, json_structure, addends = []):
        """
        @param json_structure:      A dict, an example client JSON object structure
        @param addends:             Optional json_validator_wrapper instances that
                                    should be added to this structure
        """
        self.json_structure = json_structure
        self.addends = addends
    
    def __add__(self, other):
        """
        >>> w = json_validator_wrapper({'a':int})
        >>> sum = w + {'b':str} # Now sum requires an int 'a' and a string 'b'
        >>> json_validate(sum)(lambda self, x: x)(None, {'a':1, 'b':'foo'})
        {'a': 1, 'b': 'foo'}
        >>> json_validate(sum)(lambda self, x: x)(None, {'a':1})
        Traceback (most recent call last):
            ...
        JSONException: Key error:  missing key client_json['b']
        >>> # Wrap a function with the validator, and call the wrapped function with a JSON obj
        >>> json_validate(sum)(lambda self, x: x)(None, {'a':1, 'b':'foo'})
        {'a': 1, 'b': 'foo'}
        >>> json_validate(sum)(lambda self, x: x)(None, {'b':'foo','a':'wrong, this is a string'})
        Traceback (most recent call last):
            ...
        JSONException: Type error: client_json['a'] = 'wrong, this is a string', which is of type str.  A value of type int is required
        """
        # TODO: ensure addends share no keys with self.json_structure
        
        if not isinstance(other, json_validator_wrapper):
            # Treat Python dicts as 'required' instances
            other = required(other)
        
        # Return a copy of self, but with 'other' included in addends.
        # Returned object should be of same type as self.
        return self.__class__(self.json_structure, self.addends + [other])
    
    def required_keys(self):
        """
        @return:    List of required keys
        >>> w = json_validator_wrapper({'a':int})
        >>> w.required_keys()
        ['a']
        """
        if isinstance(self, (atleast_one, one_of)):
            my_keys = []
        # My one_of keys plus those of any addends
        elif isinstance(self, required) or isinstance(self.json_structure, dict):
            my_keys = self.json_structure.keys()
        else:
            my_keys = []
        
        return list(
            set(my_keys).union(
                reduce(
                    operator.add, [
                        addend.required_keys() for addend in self.addends
                    ], []
                )
            )
        )
        
    def one_of_keys(self):
        """
        @return:    List of lists of one_of keys.  Client JSON object must contain
                    exactly one key in each list.
        """
        rv = [self.json_structure.keys()] if isinstance(self, one_of) else []
        
        for addend in self.addends:
            addend_one_of_keys = addend.one_of_keys()
            if addend_one_of_keys:
                rv.extend(addend_one_of_keys)
        
        return rv

    def atleast_one_keys(self):
        ret = [self.json_structure.keys()] if isinstance(self, atleast_one) else []
        
        for addend in self.addends:
            more = addend.atleast_one_keys()
            if more: ret.extend(more)

        return ret
    
    def __getitem__(self, k):
        """
        Get the expected JSON structure for a given key.  E.g.:
        >>> (one_of({'a':int, 'b':int}) + required({'c':str}))['c']
        <type 'str'>
        """
        if k in self.json_structure:
            return self.json_structure[k]
        else:
            for addend in self.addends:
                try:
                    return addend[k]
                except KeyError:
                    pass
        
        # Not in self or any addends
        raise KeyError(k)


class one_of(json_validator_wrapper):
    """
    Validates a JSON object (that is, a dictionary) that has exactly one of the
    provided keys.  E.g., if you have a function like:
    
    @json_validate(one_of({'a': int, 'b': [str]}))
    def foo():
        pass
    
    Then you can call foo({'a': 1}) or foo({'b': ['str1', 'str2']}), but not
    foo({}) nor foo({'a': 1, 'b': ['str1', 'str2']}).
    
    You can also combine required keys with one_of keys like so:
    
    @json_validate(one_of({'a': int, 'b': [str]}) + required({'c': str, 'd': str}))
    def bar():
        pass
    """
    pass

class atleast_one(json_validator_wrapper):
    pass

class required(json_validator_wrapper):
    """
    Validates a JSON object (that is, a dictionary) that has all of the
    provided keys.  E.g., if you have a function like:
    
    @json_validate(required({'c': str, 'd': str}))
    def quux():
        pass
    
    Then you can call quux({'c': 'A string', 'd': 'Another string'}) but not
    quux({'c': 'A string'}) nor quux({'d': 'Another string'}).
    
    You can combine required keys with one_of keys; see the one_of docstring.
    Incidentally, a 'required' instance is treated just like a normal dict by
    json_validate(); the only advantage of a 'required' instance is it can be
    added to other required and one_of instances.
    """
    pass


def do_validate(json_structure, client_json, path='client_json'):
    """
    Validate that the client input matches a view's required input
    @param json_structure:  An example json object, the kind of input the wrapped
                            function requires.
    @param client_json:     An object passed in from a client
    @param path:           Optional string describing the path from the root of
                            the JSON structure to the current node (supports
                            recursion)
    """
    # Watch the order of these if-statements, check for derived classes before
    # base classes.
    if isinstance(json_structure, json_validator_wrapper):
        assert_json_type(dict, client_json, path)
        
        # Validate all required keys
        for key in json_structure.required_keys():
            # Get the value for key from the wrapped JSON structure
            next_json_structure = json_structure[key]
            if not isinstance(next_json_structure, optional):
                assert_json(key in client_json, "Key error:  missing key %s[%s]" % (
                    path, repr(key)
                ))
                
                do_validate(next_json_structure, client_json[key], path + '[%s]' % repr(key))
            
            elif key in client_json:
                do_validate(next_json_structure, client_json[key], path + '[%s]' % repr(key))
        
        # Validate all one_of keys.  They're formatted as a list of lists of keys.
        for one_of_keys in json_structure.one_of_keys():
            keys_in_client_json = [
                key for key in one_of_keys
                if key in client_json
            ]
            
            if len(keys_in_client_json) == 0:
                raise JSONException("%s requires one of these keys, but found none: %s" % (
                    path, one_of_keys
                ))
            elif len(keys_in_client_json) > 1:
                raise JSONException("%s requires one of these keys: %s, but found several: %s" % (
                    path, one_of_keys, keys_in_client_json
                ))
            
            key = keys_in_client_json[0]
            # Get the value for key from the wrapped JSON structure
            next_json_structure = json_structure[key]
            do_validate(next_json_structure, client_json[key], path + '[%s]' % repr(key))

        for atleast_one_keys in json_structure.atleast_one_keys():
            keys_in_client_json = [
                key for key in atleast_one_keys
                if key in client_json
            ]
            
            if len(keys_in_client_json) == 0:
                raise JSONException("%s requires *at least* one of these keys, but found none: %s" % (
                    path, atleast_one_keys
                ))
            
            key = keys_in_client_json[0]
            # Get the value for key from the wrapped JSON structure
            next_json_structure = json_structure[key]
            do_validate(next_json_structure, client_json[key], path + '[%s]' % repr(key))
    
    elif isinstance(json_structure, dict):
        # Treat regular Python dicts the same as 'required' instances
        do_validate(required(json_structure), client_json, path)
    
    elif isinstance(json_structure, list):
        # We expect client_json to be a list:  any number of elements in client_json is ok
        assert_json_type(list, client_json, path)
        assert len(json_structure) == 1, "lists in json_validate structures must have exactly one element"
        for i, client_value in enumerate(client_json):
            do_validate(json_structure[0], client_value, path + '[%s]' % i)
    
    elif isinstance(json_structure, optional):
        # OK if client_json is falsy
        if client_json:
            # Validate the optional value
            do_validate(json_structure.value, client_json, path)
    
    elif json_structure == anytype:
        # client_json can be anything, including None -- we already know it's present
        pass
    
    elif type(json_structure) is type:
        # For example, json_structure might be 'int', have type 'type'.
        # Make sure client_json is an int
        assert_json_type(json_structure, client_json, path)
        
    elif hasattr(json_structure, 'match'):
        # json_structure is a compiled regular expression
        assert_json(
            json_structure.match(client_json),
            'Format error:  %s = %s, does not match required pattern %s' % (
                path, repr(client_json), repr(json_structure.pattern)
            )
        )
    else:
        raise TypeError('json_structure argument %s is of prohibited type' % repr(json_structure))


def json_validate(json_structure):
    """
    Generalize a way to validate JSON fields.  The json_structure parameter is
    a description of the kind of client input the wrapped function requires.
    The json_structure allowed values are <type 'int'>, <type 'float'>,
    <type 'str'>, a list, a dict, or a compiled regular expression.  If
    json_structure is a list or dict, each of its members must also be one of
    the allowed values, and so on recursively.
    
    The wrapper will throw a JSONException if the client-input JSON
    does not have a structure that matches json_structure.  Ergo:

    If json_structure is <type 'str'>, client input must be a string or unicode.

    If json_structure is <type 'int'>, client input must be an int, or a float
    with no fractional part.
    
    If json_structure is <type 'float'>, client input must be an int or a float.
    
    If json_structure is a compiled regular expression, client input must be a
    matching string.  (Remember to end your regex with '$'.)
    
    If json_structure is a list with one member, client input must be a list of
    zero or more members, each client-input member matching the json_structure
    member.
    
    If json_structure is a dict, client input must be a dict (a JSON 'object')
    with the all the keys json_structure has, and its values must match the
    corresponding json_structure values.  The validator ignores extra keys in
    client input.
    
    @param json_structure:  An example json object, the kind of input the wrapped
                            function requires.

    >>> json_structure = required({'must_be_here':str}) + {
    ...     'a': int,
    ...     'b': float,
    ...     'c': str,
    ...     'd': [int],
    ...     'e': anytype,
    ...     'f': {'nested_structure': float},
    ...     'g': re.compile('^fo*$'),
    ...     'h': one_of({'a': int, 'b': list}),
    ...     'i': atleast_one({'a': int, 'b': list}),
    ... } + atleast_one({'more_keys': str})
    >>> class C:
    ...     @json_validate(json_structure)
    ...     def fn(self, input):
    ...         print input
    ... 
    >>> c = C()
    >>> c.fn({
    ...     'must_be_here': 'is here',
    ...     'a': 1.0,
    ...     'b': 2.5,
    ...     'c': 'Hi!',
    ...     'd': [1, 2, 3],
    ...     'e': [],
    ...     'f': {'nested_structure': 5},
    ...     'extra_key': 'this is ignored by json_validate',
    ...     'g': 'fooooooooo',
    ...     'h': {'b':['anything','is','OK','here']},
    ...     'i': {'a':1},
    ...     'more_keys': ''
    ... })
    {'must_be_here': 'is here', 'a': 1.0, 'c': 'Hi!', 'b': 2.5, 'e': [], 'd': [1, 2, 3], 'g': 'fooooooooo', 'f': {'nested_structure': 5}, 'i': {'a': 1}, 'h': {'b': ['anything', 'is', 'OK', 'here']}, 'more_keys': '', 'extra_key': 'this is ignored by json_validate'}
    >>> 
    >>> c.fn({
    ...     'must_be_here': 'is here',
    ...     'a': 1.0,
    ...     'b': 2.5,
    ...     'c': 'Hi!',
    ...     'd': [1, 2, 3],
    ...     'e': [],
    ...     'f': {'nested_structure': 5},
    ...     'extra_key': 'this is ignored by json_validate',
    ...     'g': 'fooooooooo',
    ...     'h': {'b':['anything','is','OK','here']},
    ...     'i': {'a':1},
    ... })
    Traceback (most recent call last):
      File "<stdin>", line 12, in <module>
      File "json_validate.py", line 455, in validator
        'h': {'b':['anything','is','OK','here']},
    json_validate.JSONException: client_json requires *at least* one of these keys, but found none: ['more_keys']
    >>> 
    >>> c.fn({
    ...     'must_be_here': 'is here',
    ...     'a': 1.0,
    ...     'b': 2.5,
    ...     'c': 'Hi!',
    ...     'd': [1, 2, 3],
    ...     'e': [],
    ...     'f': {'nested_structure': 5},
    ...     'extra_key': 'this is ignored by json_validate',
    ...     'g': 'fooooooooo',
    ...     'h': {'a':1,'b':["shouldn't have both a and b"]},
    ...     'i': {'a':1},
    ...     'more_keys': ''
    ... })
    Traceback (most recent call last):
      File "<stdin>", line 13, in <module>
      File "json_validate.py", line 455, in validator
        'h': {'b':['anything','is','OK','here']},
    json_validate.JSONException: client_json['h'] requires one of these keys: ['a', 'b'], but found several: ['a', 'b']
    """
    # the decorator
    def validator_wrapper(f):
        @functools.wraps(f)
        def validator(self, json):
            try:
                do_validate(json_structure, json)
            except JSONException as e:
                # If JSON doesn't validate, add some extra debugging info and re-raise
                e.client_json = json
                e.json_structure = json_structure
                raise e
            
            # JSON validated.
            return f(self, json)
            
        # Save json_structure for future use, as in doc_view.py
        f.json_structure = json_structure
        # Bubble up the undecorated_function attribute, even if f is already decorated
        validator.undecorated_function = getattr(f, 'undecorated_function', f)
        return validator
    return validator_wrapper
        
def json_validate_warn(json_structure):
    """
    Like json_validate, but only logs warning on validation failure.
    """
    # the decorator
    def validate_warner(f):
        # the function
        @functools.wraps(f)
        def validate_warner(json,context,*args,**kwargs):
            warning = None
            try:
                do_validate(json_structure, json)
            except JSONException as e:
                # Didn't validate
                warning = str(e)
                url, method = '?', '?'
                if 'rq' in context:
                    method = context['rq'].method
                    url = context['rq'].get_full_path()
                
                # Don't re-raise, just warn
                logging.warn('%s %s: %s' % (
                    method, url, warning
                ))
            
            rv = f(json,context,*args,**kwargs)
            
            if warning:
                try:
                    # Tell client about validation error
                    rv['warning'] = warning
                except Exception as e:
                    logging.error(e)
            
            return rv
        
        # Save json_structure for future use, as in doc_view.py
        f.json_structure = json_structure
        # Bubble up the undecorated_function attribute, even if f is already decorated
        validate_warner.undecorated_function = getattr(f, 'undecorated_function', f)
        return functools.update_wrapper(validate_warner, f)
    return validate_warner

if __name__ == "__main__":
    import doctest
    doctest.testmod()
