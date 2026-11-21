"""Describe nested work arguments from the capabilities actually offered."""

WORK_FIELDS = frozenset({'capability', 'prompt', 'purpose', 'version', 'seconds', 'sources', 'knowledge'})


def selection_schemas(capabilities, offered, event_kinds):
    def obj(properties, required=()):
        return {'type': 'object', 'properties': properties, 'required': list(required), 'additionalProperties': False}

    offered = list(offered)
    definitions = {}
    references = {name: {'$ref': '#/$defs/capability_'+str(index)} for index, name in enumerate(offered)}

    def work(names):
        variants = [references[name] for name in names]
        return {'oneOf': variants} if variants else False

    for index, name in enumerate(offered):
        cap = capabilities[name]
        native = cap['kind'] == 'native'
        properties = {
            'capability': {'const': name},
            'prompt': {'type': 'string'} if native else {},
            'purpose': {'description': 'Purpose retained with the assignment; normally a short string.'},
            'seconds': ({'type': ['integer', 'null'], 'minimum': 1, 'maximum': (2**64-1)//1000}
                        if native else {'type': ['number', 'null'], 'exclusiveMinimum': 0}),
            'sources': {'type': 'object', 'additionalProperties': {'type': 'string'},
                        'description': 'New input name -> retained source object ID; reserved names cannot be replaced.'},
            'knowledge': {'type':'array','items':{'type':'string'},
                          'description':'Retained knowledge IDs to inspect with their explicit correction relations at dispatch. The host does not choose which interpretation applies.'},
        }
        if not cap.get('strategy_files'):
            properties['version'] = {'type': 'string', 'description': 'Research version object ID; omission retains the admitted version.'}
        assert set(properties) <= WORK_FIELDS
        definitions['capability_'+str(index)] = obj(properties, ('capability', 'prompt') if native else ('capability',))

    any_work = work(offered)
    leaf_work = work(name for name in offered if capabilities[name]['kind'] != 'native' or not capabilities[name].get('select'))
    return_work = work(name for name in offered if capabilities[name]['kind'] == 'native' and capabilities[name].get('select'))
    call = obj({'work': leaf_work, 'return_to': return_work}, ('work', 'return_to'))
    call['description'] = 'Both values are work specification OBJECTS, not choice IDs. The return target is an offered native capability with selection authority.'
    nullable = lambda schema: {'anyOf': [schema, {'type': 'null'}]}
    condition = obj({'event': {'anyOf': [{'type': 'boolean'}, {'enum': list(event_kinds)}], 'default': True},
                     'after': {'type': ['number', 'null'], 'description': 'Finite Unix timestamp, not a duration.'}})
    wait = obj({'condition': condition, 'work': nullable(any_work), 'call': nullable({'$ref': '#/$defs/call'})}, ('condition',))
    wait['anyOf'] = [{'properties': {name: {'type': 'null'}}} for name in ('work', 'call')]
    wait['description'] = 'At most one work or call. Omitting both deliberately waits without scheduling work.'
    identity = {'type': 'string', 'minLength': 1, 'description': 'Caller-chosen idempotency key for this new selection; not an existing choice/object ID.'}
    delivery = {'type': 'string', 'description': 'delivery_token from this invocation\'s receive result.'}
    select = obj({'identity': identity, 'delivery': delivery, 'work': nullable(any_work),
                  'call': nullable({'$ref': '#/$defs/call'}), 'wait': nullable({'$ref': '#/$defs/wait'})}, ('identity', 'delivery'))
    select['oneOf'] = [{'required': [name], 'properties': {key: {'not': {'type': 'null'}} if key == name else {'type': 'null'}
                         for key in ('work', 'call', 'wait')}} for name in ('work', 'call', 'wait')]
    delegated = obj({'identity': identity, 'delivery': delivery, 'work': any_work}, ('identity', 'delivery', 'work'))
    delegated['$defs'] = definitions
    select['$defs'] = {**definitions, 'call': call, 'wait': wait}
    return {'work': delegated,
            'select': select}
