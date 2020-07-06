import dateutil, json, os, sys

import singer
from singer import utils

from .helper import generate_request, get_endpoint, get_tzinfo, get_record, get_record_list, nested_get
from . import json2schema


LOGGER = singer.get_logger()


def load_schema(config, entity):
    '''Returns the schema for the specified source'''
    schema = utils.load_json(os.path.join(config["schema_dir"], "{}.json".format(entity)))
    return schema


def load_discovered_schema(config, stream):
    '''Attach inclusion automatic to each schema'''
    schema = load_schema(config, stream.tap_stream_id)
    for k in schema['properties']:
        schema['properties'][k]['inclusion'] = 'automatic'
    return schema


def _discover_schemas(config, streams, schema):
    '''Iterate through streams, push to an array and return'''
    result = {'streams': []}
    for key in streams.keys():
        stream = streams[key]
        LOGGER.info('Loading schema for %s', stream.tap_stream_id)
        result['streams'].append({'stream': stream.tap_stream_id,
                                  'tap_stream_id': stream.tap_stream_id,
                                  'schema': load_discovered_schema(config, stream)})
    return result


def discover(config, streams):
    '''JSON dump the schemas to stdout'''
    LOGGER.info("Loading Schemas")
    json.dump(_discover_schemas(config, streams, config["schema"]), sys.stdout, indent=2)



def _do_filter(config, obj, dict_path, schema):
    if not obj:
        return None
    obj_type = nested_get(schema, dict_path + ["type"])
    obj_format = nested_get(schema, dict_path + ["format"])
    tzinfo = get_tzinfo(config)
    if obj_type is None:
        return None
    if type(obj_type) is list:
        obj_type = obj_type[1]

    if obj_type == "object":
        assert(type(obj) is dict and obj.keys())
        filtered = dict()
        for key in obj.keys():
            ret = _do_filter(config, obj[key], dict_path + ["properties", key], schema)
            if ret:
                filtered[key] = ret
    elif obj_type == "array":
        assert(type(obj) is list)
        filtered = list()
        for o in obj:
            ret = _do_filter(config, o, dict_path + ["items"], schema)
            if ret:
                filtered.append(ret)
    else:
        if obj_type == "string":
            filtered = str(obj)
            if obj_format == "date-time":
                filtered = dateutil.parser.parse(obj).replace(tzinfo=tzinfo).isoformat()
        elif obj_type == "number":
            try:
                filtered = float(obj)
            except ValueError as e:
                LOGGER.error(str(e) + "dict_path" + str(dict_path) + " object type: " + obj_type)
                raise
        else:
            filtered = obj
    return filtered


def filter_result(config, row, schema):
    return _do_filter(config, row, [], schema)


def infer_schema(config, streams, out_catalog=True, add_tstamp=True):
    """
    Infer schema from the sample record list and write JSON schema and
    catalog files under schema directory and catalog directory.
    To fully support multiple streams, the catalog files must be consolidated
    but that is not supported in this function yet.
    """
    # TODO: Support multiple streams specified by STREAM[]
    tap_stream_id = streams[list(streams.keys())[0]].tap_stream_id

    params = config
    page_number = 0
    offset_number = 0
    params.update({"current_page": page_number})
    params.update({"current_offset": offset_number})
    endpoint = get_endpoint(config, tap_stream_id, params)
    LOGGER.info("GET %s", endpoint)
    auth_method = config.get("auth_method", "basic")
    data = generate_request(config, tap_stream_id, endpoint, auth_method)

    # In case the record is not at the root level
    data = get_record_list(data, config.get("record_list_level"))

    schema = json2schema.infer_schema(data, config.get("record_level"))
    if add_tstamp:
        schema["properties"]["_etl_tstamp"] = {"type": ["null", "integer"]}

    with open(os.path.join(config["schema_dir"], tap_stream_id + ".json"), "w") as f:
        json.dump(schema, f, indent=2)

    if out_catalog:
        schema["selected"] = True
        catalog = {"streams": [{"stream": tap_stream_id,
                                "tap_stream_id": tap_stream_id,
                                "schema": schema
                                }]}
        with open(os.path.join(config["catalog_dir"], tap_stream_id + ".json"), "w") as f:
            json.dump(catalog, f, indent=2)