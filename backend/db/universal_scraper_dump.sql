--
-- PostgreSQL database dump
--


-- Dumped from database version 18.4 (Debian 18.4-1.pgdg12+1)
-- Dumped by pg_dump version 18.4

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: __msar; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA __msar;


--
-- Name: mathesar_types; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA mathesar_types;


--
-- Name: msar; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA msar;


--
-- Name: uuid-ossp; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS "uuid-ossp" WITH SCHEMA public;


--
-- Name: EXTENSION "uuid-ossp"; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION "uuid-ossp" IS 'generate universally unique identifiers (UUIDs)';


--
-- Name: vector; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;


--
-- Name: EXTENSION vector; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION vector IS 'vector data type and ivfflat and hnsw access methods';


--
-- Name: pkey_kind; Type: TYPE; Schema: msar; Owner: -
--

CREATE TYPE msar.pkey_kind AS ENUM (
    'UUIDv4',
    'IDENTITY'
);


--
-- Name: col_def; Type: TYPE; Schema: __msar; Owner: -
--

CREATE TYPE __msar.col_def AS (
	name_ text,
	type_ text,
	not_null boolean,
	default_ text,
	pkey_type msar.pkey_kind,
	description text
);


--
-- Name: con_def; Type: TYPE; Schema: __msar; Owner: -
--

CREATE TYPE __msar.con_def AS (
	name_ text,
	type_ "char",
	col_names text[],
	deferrable_ boolean,
	fk_rel_name text,
	fk_col_names text[],
	fk_upd_action "char",
	fk_del_action "char",
	fk_match_type "char",
	expression text
);


--
-- Name: not_null_def; Type: TYPE; Schema: __msar; Owner: -
--

CREATE TYPE __msar.not_null_def AS (
	col_name text,
	not_null boolean
);


--
-- Name: email; Type: DOMAIN; Schema: mathesar_types; Owner: -
--

CREATE DOMAIN mathesar_types.email AS text
	CONSTRAINT email_check CHECK ((VALUE ~ '^[a-zA-Z0-9.!#$%&''*+/=?^_`{|}~-]+@[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*$'::text));


--
-- Name: mathesar_json_array; Type: DOMAIN; Schema: mathesar_types; Owner: -
--

CREATE DOMAIN mathesar_types.mathesar_json_array AS jsonb
	CONSTRAINT mathesar_json_array_check CHECK ((jsonb_typeof(VALUE) = 'array'::text));


--
-- Name: mathesar_json_object; Type: DOMAIN; Schema: mathesar_types; Owner: -
--

CREATE DOMAIN mathesar_types.mathesar_json_object AS jsonb
	CONSTRAINT mathesar_json_object_check CHECK ((jsonb_typeof(VALUE) = 'object'::text));


--
-- Name: mathesar_money; Type: DOMAIN; Schema: mathesar_types; Owner: -
--

CREATE DOMAIN mathesar_types.mathesar_money AS numeric;


--
-- Name: multicurrency_money; Type: TYPE; Schema: mathesar_types; Owner: -
--

CREATE TYPE mathesar_types.multicurrency_money AS (
	value numeric,
	currency character(3)
);


--
-- Name: uri; Type: DOMAIN; Schema: mathesar_types; Owner: -
--

CREATE DOMAIN mathesar_types.uri AS text
	CONSTRAINT uri_check CHECK (((VALUE IS NULL) OR (array_position(regexp_match(VALUE, '^(?:([^:/?#]+):)?(?://(?:[^/?#]*))?([^?#]*)(?:\?(?:[^#]*))?(?:#(?:.*))?'::text), NULL::text) IS NULL)));


--
-- Name: joinable_tables; Type: TYPE; Schema: msar; Owner: -
--

CREATE TYPE msar.joinable_tables AS (
	base bigint,
	target bigint,
	join_path jsonb,
	fkey_path jsonb,
	depth integer,
	multiple_results boolean
);


--
-- Name: type_compat_details; Type: TYPE; Schema: msar; Owner: -
--

CREATE TYPE msar.type_compat_details AS (
	type_compatible boolean,
	mathesar_casting boolean,
	group_sep "char",
	decimal_p "char",
	curr_pref text,
	curr_suff text
);


--
-- Name: add_constraints(text, __msar.con_def[]); Type: FUNCTION; Schema: __msar; Owner: -
--

CREATE FUNCTION __msar.add_constraints(tab_name text, VARIADIC con_defs __msar.con_def[]) RETURNS text
    LANGUAGE plpgsql STRICT
    AS $$/*
Add the given constraints to the given table.

Args:
  tab_name: Fully-qualified, quoted table name.
  con_defs: The constraints to be added.
*/
DECLARE
  add_con_sql text;
BEGIN
  WITH con_cte AS (
    SELECT string_agg('ADD ' || __msar.build_con_def_text(con), ', ') as con_additions
    FROM unnest(con_defs) as con
  )
  SELECT format('ALTER TABLE %s %s', tab_name, con_additions) INTO add_con_sql FROM con_cte;
  EXECUTE add_con_sql;
  RETURN add_con_sql;
END;
$$;


--
-- Name: add_table(text, __msar.col_def[], __msar.con_def[]); Type: FUNCTION; Schema: __msar; Owner: -
--

CREATE FUNCTION __msar.add_table(tab_name text, col_defs __msar.col_def[], con_defs __msar.con_def[]) RETURNS text
    LANGUAGE plpgsql
    AS $$/*
Add a table, returning the command executed.

Args:
  tab_name: A qualified & quoted name for the table to be added.
  col_defs: An array of __msar.col_def defining the column set of the new table.
  con_defs (optional): An array of __msar.con_def defining the constraints for the new table.

Note: Even if con_defs is null, there can be some column-level constraints set in col_defs.
*/
DECLARE
  add_tab_sql text;
BEGIN
  WITH col_cte AS (
    SELECT string_agg(__msar.build_col_def_text(col), ', ') AS table_columns
    FROM unnest(col_defs) AS col
  ), con_cte AS (
    SELECT string_agg(__msar.build_con_def_text(con), ', ') AS table_constraints
    FROM unnest(con_defs) as con
  )
  SELECT format(
    'CREATE TABLE %s (%s)',
    tab_name,
    concat_ws(', ', table_columns, table_constraints)
  ) INTO add_tab_sql
  FROM col_cte, con_cte;
  EXECUTE add_tab_sql;
  RETURN add_tab_sql;
END;
$$;


--
-- Name: build_col_def_text(__msar.col_def); Type: FUNCTION; Schema: __msar; Owner: -
--

CREATE FUNCTION __msar.build_col_def_text(col __msar.col_def) RETURNS text
    LANGUAGE sql
    AS $$/*
Build appropriate text defining the given column for table creation or alteration.
*/
SELECT format(
  '%s %s %s %s %s',
  col.name_,
  col.type_,
  CASE WHEN col.not_null THEN 'NOT NULL' END,
  'DEFAULT ' || col.default_,
  -- This can be used to define our default Mathesar primary key column.
  -- TODO: We should really consider doing GENERATED *ALWAYS* (rather than BY DEFAULT), but this
  -- breaks some other assumptions.
  CASE col.pkey_type
    WHEN 'IDENTITY' THEN 'GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY'
    WHEN 'UUIDv4' THEN 'PRIMARY KEY DEFAULT gen_random_uuid()'
  END
);
$$;


--
-- Name: build_con_def_text(__msar.con_def); Type: FUNCTION; Schema: __msar; Owner: -
--

CREATE FUNCTION __msar.build_con_def_text(con __msar.con_def) RETURNS text
    LANGUAGE sql STRICT
    AS $$/*
Build appropriate text defining the given constraint for table creation or alteration.

If the given con.name_ is null, the syntax changes slightly (we don't add 'CONSTRAINT'). The FOREIGN
KEY constraint has a number of extra strings that may or may not be appended.  The best
documentation for this is the FOREIGN KEY section of the CREATE TABLE docs:
https://www.postgresql.org/docs/current/sql-createtable.html

One helpful note is that this function makes use heavy of the || operator. This operator returns
null if either side is null, and thus

  'CONSTRAINT ' || con.name_ || ' '

is 'CONSTRAINT <name> ' when con.name_ is not null, and simply null if con.name_ is null.
*/
SELECT CASE
    WHEN con.type_ = 'u' THEN  -- It's a UNIQUE constraint
      format(
        '%sUNIQUE %s',
        'CONSTRAINT ' || con.name_ || ' ',
        __msar.build_text_tuple(con.col_names)
      )
    WHEN con.type_ = 'p' THEN  -- It's a PRIMARY KEY constraint
      format(
        '%sPRIMARY KEY %s',
        'CONSTRAINT ' || con.name_ || ' ',
        __msar.build_text_tuple(con.col_names)
      )
    WHEN con.type_ = 'f' THEN  -- It's a FOREIGN KEY constraint
      format(
        '%sFOREIGN KEY %s REFERENCES %s%s%s%s%s',
        'CONSTRAINT ' || con.name_ || ' ',
        __msar.build_text_tuple(con.col_names),
        con.fk_rel_name,
        __msar.build_text_tuple(con.fk_col_names),
        ' MATCH ' || msar.get_fkey_match_type_from_char(con.fk_match_type),
        ' ON DELETE ' || msar.get_fkey_action_from_char(con.fk_del_action),
        ' ON UPDATE ' || msar.get_fkey_action_from_char(con.fk_upd_action)
      )
    ELSE
      NULL
  END
  || CASE WHEN con.deferrable_ THEN 'DEFERRABLE' ELSE '' END;
$$;


--
-- Name: build_qualified_name_sql(text, text); Type: FUNCTION; Schema: __msar; Owner: -
--

CREATE FUNCTION __msar.build_qualified_name_sql(sch_name text, obj_name text) RETURNS text
    LANGUAGE plpgsql STRICT
    AS $$/*
Return the fully-qualified, properly quoted, name for a given database object (e.g., table).

Args:
  sch_name: The schema of the object, unquoted.
  obj_name: The name of the object, unqualified and unquoted.
*/
BEGIN
  RETURN  format('%I.%I', sch_name, obj_name);
END;
$$;


--
-- Name: build_text_tuple(text[]); Type: FUNCTION; Schema: __msar; Owner: -
--

CREATE FUNCTION __msar.build_text_tuple(text[]) RETURNS text
    LANGUAGE sql STRICT
    AS $_$
SELECT '(' || string_agg(col, ', ') || ')' FROM unnest($1) x(col);
$_$;


--
-- Name: build_typmodin_arg(jsonb, boolean); Type: FUNCTION; Schema: __msar; Owner: -
--

CREATE FUNCTION __msar.build_typmodin_arg(typ_options jsonb, timespan_flag boolean) RETURNS cstring[]
    LANGUAGE sql STRICT
    AS $$/*
Build an array to be used as the argument for a typmodin function.

Timespans have to be handled slightly differently since they have a tricky `fields` argument that
requires special processing. See __msar.prepare_fields_arg for more details.

Args:
  typ_options: JSONB giving options fields as per the description in msar.build_type_text.
  timespan_flag: true if the associated type is a timespan, false otherwise.
*/
SELECT array_remove(
  ARRAY[
    typ_options ->> 'length',
    CASE WHEN timespan_flag THEN __msar.prepare_fields_arg(typ_options ->> 'fields') END,
    typ_options ->> 'precision',
    typ_options ->> 'scale'
  ],
  null
)::cstring[]
$$;


--
-- Name: exec_dql(text); Type: FUNCTION; Schema: __msar; Owner: -
--

CREATE FUNCTION __msar.exec_dql(command text) RETURNS jsonb
    LANGUAGE plpgsql STRICT
    AS $$/*
Execute the given command, returning a JSON object describing the records in the following form:
[
  {"id": 1, "col1_name": "value1", "col2_name": "value2"},
  {"id": 2, "col1_name": "value1", "col2_name": "value2"},
  {"id": 3, "col1_name": "value1", "col2_name": "value2"},
  ...
]

Useful for SELECTing from tables. Most useful when you're performing DQL.

Note that you must include the primary key column(`id` in case of a Mathesar table) in the
command_template if you want the returned records to be uniquely identifiable.

Args:
  command: Raw string that will be executed as a command.
*/
DECLARE
  records jsonb;
BEGIN
  EXECUTE 'WITH cte AS (' || command || ')
  SELECT jsonb_agg(row_to_json(cte.*)) FROM cte' INTO records;
  RETURN records;
END;
$$;


--
-- Name: exec_dql(text, anyarray); Type: FUNCTION; Schema: __msar; Owner: -
--

CREATE FUNCTION __msar.exec_dql(command_template text, VARIADIC arguments anyarray) RETURNS jsonb
    LANGUAGE plpgsql STRICT
    AS $$/*
Execute a templated command, returning a JSON object describing the records in the following form:
[
  {"id": 1, "col1_name": "value1", "col2_name": "value2"},
  {"id": 2, "col1_name": "value1", "col2_name": "value2"},
  {"id": 3, "col1_name": "value1", "col2_name": "value2"},
  ...
]

The template is given in the first argument, and all further arguments are used to fill in the
template. Useful for SELECTing from tables. Most useful when you're performing DQL.

Note that you must include the primary key column(`id` in case of a Mathesar table) in the
command_template if you want the returned records to be uniquely identifiable.

Args:
  command_template: Raw string that will be executed as a command.
  arguments: arguments that will be used to fill in the template.
*/
DECLARE formatted_command TEXT;
BEGIN
  formatted_command := format(command_template, VARIADIC arguments);
  RETURN __msar.exec_dql(formatted_command);
END;
$$;


--
-- Name: get_formatted_base_type(text, jsonb); Type: FUNCTION; Schema: __msar; Owner: -
--

CREATE FUNCTION __msar.get_formatted_base_type(typ_name text, typ_options jsonb) RETURNS text
    LANGUAGE plpgsql
    AS $$ /*
Build the appropriate type definition string, without Array brackets.

This function uses some PostgreSQL internal functions to do its work. In particular, for any type
that takes options, This function uses the typmodin (read "type modification input") system
functions to convert the given options into a typmod integer. The typ_name given is converted into
the OID of the named type. These two pieces let us call `format_type` to get a canonical string
representation of the definition of the type, with its options.

Args:
  typ_name: This should be qualified and quoted as needed.
  typ_options: These should be in the form described in msar.build_type_text.
*/
DECLARE
  typ_id oid;
  timespan_flag boolean;
  typmodin_func text;
  typmod integer;
BEGIN
  -- Here we just get the OID of the type.
  typ_id := typ_name::regtype::oid;
  -- This is a lookup of the function name for the typmodin function associated with the type, if
  -- one exists.
  typmodin_func := typmodin::text FROM pg_catalog.pg_type WHERE oid=typ_id AND typmodin<>0;
  -- This flag is needed since timespan types need special handling when converting the options into
  -- the form needed to call the typmodin function.
  timespan_flag := typcategory='T' FROM pg_catalog.pg_type WHERE oid=typ_id;
  IF (
    jsonb_typeof(typ_options) = 'null'  -- The caller passed no type options
    OR typ_options IS NULL -- The caller didn't even pass the type options key
    OR typ_options='{}'::jsonb  -- The caller passed an empty type options object
    OR typmodin_func IS NULL  -- The type doesn't actually accept type options
  ) THEN
    typmod := NULL;
  ELSE
    -- Here, we actually run the typmod function to get the output for use in the format_type call.
    EXECUTE format(
      'SELECT %I(%L)',
      typmodin_func,
      __msar.build_typmodin_arg(typ_options, timespan_flag)
    ) INTO typmod;
  END IF;
  RETURN format_type(typ_id::integer, typmod::integer);
END;
$$;


--
-- Name: get_qualified_relation_name(oid); Type: FUNCTION; Schema: __msar; Owner: -
--

CREATE FUNCTION __msar.get_qualified_relation_name(rel_id oid) RETURNS text
    LANGUAGE plpgsql STRICT
    AS $$/*
Return the name for a given relation (e.g., table), qualified or quoted as appropriate.

In cases where the relation is already included in the search path, the returned name will not be
fully-qualified.

The relation *must* be in the pg_class table to use this function.

Args:
  rel_id: The OID of the relation.
*/
BEGIN
  RETURN rel_id::regclass::text;
END;
$$;


--
-- Name: get_qualified_relation_name_or_null(oid); Type: FUNCTION; Schema: __msar; Owner: -
--

CREATE FUNCTION __msar.get_qualified_relation_name_or_null(rel_id oid) RETURNS text
    LANGUAGE sql STRICT
    AS $$/*
Return the name for a given relation (e.g., table), qualified or quoted as appropriate.

In cases where the relation is already included in the search path, the returned name will not be
fully-qualified.

The relation *must* be in the pg_class table to use this function. This function will return NULL if
no corresponding relation can be found.

Args:
  rel_id: The OID of the relation.
*/
SELECT CASE
  WHEN EXISTS (SELECT oid FROM pg_catalog.pg_class WHERE oid=rel_id) THEN rel_id::regclass::text
END
$$;


--
-- Name: jsonb_key_exists(jsonb, text); Type: FUNCTION; Schema: __msar; Owner: -
--

CREATE FUNCTION __msar.jsonb_key_exists(data jsonb, key text) RETURNS boolean
    LANGUAGE plpgsql
    AS $$/*
Wraps the `?` jsonb operator for improved readability.
*/
  BEGIN
    RETURN data ? key;
  END;
$$;


--
-- Name: prepare_fields_arg(text); Type: FUNCTION; Schema: __msar; Owner: -
--

CREATE FUNCTION __msar.prepare_fields_arg(fields text) RETURNS text
    LANGUAGE sql
    AS $$/*
Convert the `fields` argument into an integer for use with the integertypmodin system function.

Args:
  fields: A string corresponding to the documented options from the doumentation at
          https://www.postgresql.org/docs/13/datatype-datetime.html

In order to construct the argument for intervaltypmodin, needed for constructing the typmod value
for INTERVAL types with arguments, we need to apply a transformation to the correct integer. This
transformation is quite arcane, and is lifted straight from the PostgreSQL C code. Given a non-null
fields argument, the steps are:
- Assign each substring of valid `fields` arguments the correct integer (from the Postgres src).
- Apply a bitshift mapping each integer to the according power of 2.
- Sum the results to get an integer signifying the fields argument.
*/
SELECT COALESCE(
  sum(1<<code)::text,
  '32767'  -- 0x7FFF in decimal; This represents no field argument.
)
FROM (
  VALUES
    ('MONTH', 1),
    ('YEAR', 2),
    ('DAY', 3),
    ('HOUR', 10),
    ('MINUTE', 11),
    ('SECOND', 12)
) AS field_map(field, code)
WHERE fields ILIKE '%' || field || '%';
$$;


--
-- Name: process_col_def_jsonb(oid, jsonb, boolean); Type: FUNCTION; Schema: __msar; Owner: -
--

CREATE FUNCTION __msar.process_col_def_jsonb(tab_id oid, col_defs jsonb, raw_default boolean) RETURNS __msar.col_def[]
    LANGUAGE sql
    AS $$/*
Create an __msar.col_def from a JSON array of column creation defining JSON blobs.

Args:
  tab_id: The OID of the table where we'll create the columns
  col_defs: A jsonb array defining a column creation (must have "type" key; "name",
                  "not_null", and "default" keys optional).
  raw_default: This boolean tells us whether we chould reproduce the default with or without quoting
               and escaping. True means we don't quote or escape, but just use the raw value.

The col_defs should have the form:
[
  {
    "name": <str> (optional),
    "type": {
      "name": <str> (optional),
      "options": <obj> (optional),
    },
    "not_null": <bool> (optional; default false),
    "default": <any> (optional),
    "description": <str> (optional)
  },
  {
    ...
  }
]

For more info on the type.options object, see the msar.build_type_text function. All pieces are
optional. If an empty object {} is given, the resulting column will have a default name like
'Column <n>' and type TEXT. It will allow nulls and have a null default value.
*/
WITH attnum_cte AS (
  SELECT MAX(attnum) AS m_attnum FROM pg_catalog.pg_attribute WHERE attrelid=tab_id
), col_create_cte AS (
  SELECT (
    -- build a name for the column
    COALESCE(
      quote_ident(col_def_obj ->> 'name'),
      quote_ident('Column ' || (attnum_cte.m_attnum + ROW_NUMBER() OVER ())),
      quote_ident('Column ' || (ROW_NUMBER() OVER ()))
    ),
    -- build the column type
    msar.build_type_text(col_def_obj -> 'type'),
    -- set the not_null value for the column
    col_def_obj ->> 'not_null',
    -- set the default value for the column
    CASE
      WHEN col_def_obj ->> 'default' IS NULL THEN
        NULL
      WHEN raw_default THEN
        col_def_obj ->> 'default'
      ELSE
        format('%L', col_def_obj ->> 'default')
    END,
    -- We don't allow setting the primary key column manually
    null,
    -- Set the description for the column
    quote_literal(col_def_obj ->> 'description')
  )::__msar.col_def AS col_defs
  FROM attnum_cte, jsonb_array_elements(col_defs) AS col_def_obj
  WHERE (col_def_obj ->> 'name' IS NULL OR col_def_obj ->> 'name' <> 'id')
)
SELECT array_agg(col_defs)
FROM col_create_cte;
$$;


--
-- Name: process_con_def_jsonb(oid, jsonb); Type: FUNCTION; Schema: __msar; Owner: -
--

CREATE FUNCTION __msar.process_con_def_jsonb(tab_id oid, con_create_arr jsonb) RETURNS __msar.con_def[]
    LANGUAGE sql
    AS $$/*
Create an array of  __msar.con_def from a JSON array of constraint creation defining JSON.

Args:
  tab_id: The OID of the table where we'll create the constraints.
  con_create_arr: A jsonb array defining a constraint creation (must have "type" key; "name",
                  "not_null", and "default" keys optional).


The con_create_arr should have the form:
[
  {
    "name": <str> (optional),
    "type": <str>,
    "columns": [<int:str>, <int:str>, ...],
    "deferrable": <bool> (optional),
    "fkey_relation_id": <int> (optional),
    "fkey_columns": [<int>, <int>, ...] (optional),
    "fkey_update_action": <str> (optional),
    "fkey_delete_action": <str> (optional),
    "fkey_match_type": <str> (optional),
  },
  {
    ...
  }
]
If the constraint type is "f", then we require fkey_relation_id.

Numeric IDs are preferred over textual ones where both are accepted.
*/
SELECT array_agg(
  (
    -- build the name for the constraint, properly quoted.
    quote_ident(con_create_obj ->> 'name'),
    -- set the constraint type as a single char. See __msar.build_con_def_text for details.
    con_create_obj ->> 'type',
    -- Set the column names associated with the constraint.
    msar.get_column_names(tab_id, con_create_obj -> 'columns'),
    -- Set whether the constraint is deferrable or not (boolean).
    con_create_obj ->> 'deferrable',
    __msar.get_qualified_relation_name((con_create_obj -> 'fkey_relation_id')::integer::oid),
    -- Build the array of foreign columns for an fkey constraint.
    msar.get_column_names(
      -- We validate that the given OID (if any) is correct.
      (con_create_obj -> 'fkey_relation_id')::bigint::oid,
      con_create_obj -> 'fkey_columns'
    ),
    -- The below are passed directly. They define some parameters for FOREIGN KEY constraints.
    con_create_obj ->> 'fkey_update_action',
    con_create_obj ->> 'fkey_delete_action',
    con_create_obj ->> 'fkey_match_type',
    null -- not yet implemented
  )::__msar.con_def
) FROM jsonb_array_elements(con_create_arr) AS x(con_create_obj);
$$;


--
-- Name: process_pk_col_def(text, msar.pkey_kind); Type: FUNCTION; Schema: __msar; Owner: -
--

CREATE FUNCTION __msar.process_pk_col_def(col_name text DEFAULT 'id'::text, pkey_type msar.pkey_kind DEFAULT 'IDENTITY'::msar.pkey_kind) RETURNS __msar.col_def[]
    LANGUAGE sql
    AS $$
  -- The below tuple(s) defines a default 'id' column for Mathesar. It can have a given name, type
  -- integer or uuid, it's not null, it uses the 'identity' or 'gen_random_uuid()' functionality to
  -- generate default values, has a default comment.
  SELECT CASE pkey_type
    WHEN 'IDENTITY' THEN
      ARRAY[
        (col_name, 'integer', true, null, pkey_type, 'Mathesar default integer ID column')
      ]::__msar.col_def[]
    WHEN 'UUIDv4' THEN
      ARRAY[
        (col_name, 'uuid', true, null, pkey_type, 'Mathesar default uuid ID column')
      ]::__msar.col_def[]
  END;
$$;


--
-- Name: add_column(regclass, jsonb, boolean); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.add_column(tab_id regclass, col_def jsonb, raw_default boolean DEFAULT false) RETURNS smallint
    LANGUAGE plpgsql STRICT
    AS $_$/*
Add a column to a table.

Args:
  tab_id: The OID of the table where we'll create the columns.
  col_def: An object defining the new column. See below for details.
  raw_default: This boolean tells us whether we chould reproduce the default with or without quoting
               and escaping. True means we don't quote or escape, but just use the raw value.

Returns:
  The attnum of the newly created column.


col_def should have the form:
  {
    "name": <str> (optional),
    "type": {
      "name": <str> (optional),
      "options": <obj> (optional),
    },
    "not_null": <bool> (optional; default false),
    "default": <any> (optional),
    "description": <str> (optional)
  }
*/
DECLARE
  unique_col_name text;
  sanitized_default text;
  created_attnum smallint;
BEGIN
  unique_col_name = msar.build_unique_column_name(tab_id, coalesce(col_def ->> 'name', 'Column'));
  sanitized_default = CASE
    WHEN col_def ->> 'default' IS NULL THEN null
    WHEN raw_default THEN col_def ->> 'default'
    ELSE format('%L', col_def ->> 'default')
  END;
  EXECUTE format(
    'ALTER TABLE %1$I.%2$I ADD COLUMN %3$I %4$s %5$s %6$s',
    msar.get_relation_schema_name(tab_id),
    msar.get_relation_name(tab_id),
    unique_col_name,
    msar.build_type_text(col_def -> 'type'),
    CASE WHEN (col_def -> 'not_null')::boolean THEN 'NOT NULL' END,
    'DEFAULT ' || sanitized_default
  );
  created_attnum = attnum
    FROM pg_catalog.pg_attribute
    WHERE attrelid = tab_id AND attname = unique_col_name;
  IF col_def ? 'description' THEN
    PERFORM msar.comment_on_column(tab_id, created_attnum, col_def ->> 'description');
  END IF;
  RETURN created_attnum;
END;
$_$;


--
-- Name: add_columns(oid, jsonb, boolean); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.add_columns(tab_id oid, col_defs jsonb, raw_default boolean DEFAULT false) RETURNS smallint[]
    LANGUAGE sql STRICT
    AS $$/*
Add columns to a table.

Args:
  tab_id: The OID of the table to which we'll add columns.
  col_defs: a JSONB array defining columns to add. See __msar.process_col_def_jsonb for details.
  raw_default: Whether to treat defaults as raw SQL. DANGER!
*/
  WITH perf_cte AS (
    SELECT msar.add_column(tab_id, col_def, raw_default) AS attnum
    FROM jsonb_array_elements(col_defs) AS col_def
  )
  SELECT array_agg(attnum) FROM perf_cte;
$$;


--
-- Name: add_constraints(oid, jsonb); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.add_constraints(tab_id oid, con_defs jsonb) RETURNS oid[]
    LANGUAGE plpgsql STRICT
    AS $$/*
Add constraints to a table.

Args:
  tab_id: The OID of the table to which we'll add constraints.
  col_defs: a JSONB array defining constraints to add. See __msar.process_con_def_jsonb for details.
*/
DECLARE
  con_create_defs __msar.con_def[];
BEGIN
  con_create_defs := __msar.process_con_def_jsonb(tab_id, con_defs);
  PERFORM __msar.add_constraints(
    __msar.get_qualified_relation_name(tab_id),
    variadic con_create_defs
  );
  RETURN array_agg(oid) FROM pg_catalog.pg_constraint WHERE conrelid=tab_id;
END;
$$;


--
-- Name: add_foreign_key_column(text, oid, oid, boolean); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.add_foreign_key_column(col_name text, rel_id oid, frel_id oid, unique_link boolean DEFAULT false) RETURNS smallint
    LANGUAGE plpgsql STRICT
    AS $$/*
Create a many-to-one or a one-to-one link between tables, returning the attnum of the newly created
column, returning the attnum of the added column.

Args:
  col_name: Name of the new column to be created in the referrer table, unquoted.
  rel_id: The OID of the referrer table, named for conrelid in the pg_attribute table.
  frel_id: The OID of the referent table, named for confrelid in the pg_attribute table.
  unique_link: Whether to make the link one-to-one instead of many-to-one.
*/
DECLARE
  pk_col_id smallint;
  col_defs jsonb;
  added_col_ids smallint[];
  con_defs jsonb;
BEGIN
  pk_col_id := msar.get_pk_column(frel_id);
  col_defs := jsonb_build_array(
    jsonb_build_object(
      'name', col_name,
      'type', jsonb_build_object('name', msar.get_column_type(frel_id, pk_col_id))
    )
  );
  added_col_ids := msar.add_columns(rel_id , col_defs , false);
  con_defs := jsonb_build_array(
    jsonb_build_object(
      'name', null,
      'type', 'f',
      'columns', added_col_ids,
      'deferrable', false,
      'fkey_relation_id', frel_id::integer,
      'fkey_columns', jsonb_build_array(pk_col_id)
    )
  );
  IF unique_link THEN
    con_defs := jsonb_build_array(
      jsonb_build_object(
        'name', null,
        'type', 'u',
        'columns', added_col_ids)
    ) || con_defs;
  END IF;
  PERFORM msar.add_constraints(rel_id , con_defs);
  RETURN added_col_ids[1];
END;
$$;


--
-- Name: add_mapping_table(oid, text, jsonb); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.add_mapping_table(sch_id oid, tab_name text, mapping_columns jsonb) RETURNS oid
    LANGUAGE plpgsql STRICT
    AS $$/*
Create a many-to-many link between tables, returning the oid of the newly created table.

Args:
  sch_id: The OID of the schema in which new referrer table is to be created.
  tab_name: Name of the referrer table to be created.
  mapping_columns: An array of objects giving the foreign key columns for the new table.

The elements of the mapping_columns array must have the form
  {"column_name": <str>, "referent_table_oid": <int>}

*/
DECLARE
  added_table_id oid;
BEGIN
  added_table_id := msar.add_mathesar_table(sch_id, tab_name, NULL, NULL, NULL, NULL, NULL) ->> 'oid';
  PERFORM msar.add_foreign_key_column(column_name, added_table_id, referent_table_oid)
  FROM jsonb_to_recordset(mapping_columns) AS x(column_name text, referent_table_oid oid);
  RETURN added_table_id;
END;
$$;


--
-- Name: add_mathesar_table(oid, text, jsonb, jsonb, jsonb, regrole, text); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.add_mathesar_table(sch_id oid, tab_name text, pk_col_def jsonb, col_defs jsonb, con_defs jsonb, own_id regrole, comment_ text) RETURNS jsonb
    LANGUAGE plpgsql
    AS $_$/*
Add a table, with a default id column, returning the OID & name of the created table.

Args:
  sch_id: The OID of the schema where the table will be created.
  tab_name (optional): The unquoted name for the new table.
  pk_col_defs (optional): The primary key column for the new table.
  col_defs (optional): The columns for the new table, in order.
  con_defs (optional): The constraints for the new table.
  own_id   (optional): The OID of the role who will own the new table.
  comment_ (optional): The comment for the new table.

Note:
  - If tab_name is NULL, the table will be created with a name in the format 'Table <n>'.
  - If col_defs is NULL, the table will still be created with a default 'id' column.
  - If an 'id' column is provided, it will be renamed to an auto-generated name.
  - There would always be an 'id' column which would be created by Mathesar.
  - If own_id is NULL, the current role will be the owner of the new table.
*/
DECLARE
  schema_name text;
  table_count integer;
  prefix text;
  uq_table_name text;
  fq_table_name text;
  created_table_id oid;
  column_defs __msar.col_def[];
  constraint_defs __msar.con_def[];
  id_col_name text;
  existing_col_names text[];
  renamed_columns jsonb := '{}'::jsonb;
BEGIN
  schema_name := msar.get_schema_name(sch_id);
  IF NULLIF(tab_name, '') IS NOT NULL AND NOT EXISTS(
      SELECT oid FROM pg_catalog.pg_class WHERE relname = tab_name AND relnamespace = sch_id
    )
  THEN
    fq_table_name := format('%I.%I', schema_name, tab_name);
  ELSE
    -- determine what prefix to use for table name generation
    IF NULLIF(tab_name, '') IS NOT NULL THEN
      prefix := tab_name || ' ';
    ELSE
      prefix := 'Table ';
    END IF;
    -- generate a table name if one doesn't exist
    SELECT COUNT(*) + 1 INTO table_count
    FROM pg_catalog.pg_class
    WHERE relkind = 'r' AND relnamespace = sch_id;
    uq_table_name := prefix || table_count;
    -- avoid name collisions
    WHILE EXISTS (
      SELECT oid FROM pg_catalog.pg_class WHERE relname = uq_table_name AND relnamespace = sch_id
    ) LOOP
      table_count := table_count + 1;
      uq_table_name := prefix || table_count;
    END LOOP;
    fq_table_name := format('%I.%I', schema_name, uq_table_name);
  END IF;

  IF jsonb_path_exists(col_defs, '$[*] ? (@.name == "id")') THEN
    -- rename 'id'
    SELECT array_agg(col_def->>'name') INTO existing_col_names FROM jsonb_array_elements(col_defs) col_def;
    id_col_name := msar.get_unique_local_identifier(existing_col_names, 'id');

    col_defs := (
      SELECT jsonb_agg(
        CASE WHEN col_def->>'name' = 'id' THEN jsonb_set(col_def, '{name}', to_jsonb(id_col_name))
          ELSE col_def END
    ) FROM jsonb_array_elements(col_defs) col_def);

    renamed_columns := jsonb_build_object('id', id_col_name);
  END IF;
  column_defs := array_cat(
    __msar.process_pk_col_def(
      COALESCE(pk_col_def->>'name', 'id'),
      COALESCE(pk_col_def->>'type', 'IDENTITY')::msar.pkey_kind
    ), __msar.process_col_def_jsonb(0, col_defs, false)
  );
  constraint_defs := __msar.process_con_def_jsonb(0, con_defs);
  PERFORM __msar.add_table(fq_table_name, column_defs, constraint_defs);
  created_table_id := fq_table_name::regclass::oid;
  PERFORM msar.comment_on_table(created_table_id, comment_);
  IF own_id IS NOT NULL THEN
    PERFORM msar.transfer_table_ownership(created_table_id, own_id);
  END IF;

  RETURN jsonb_build_object(
    'oid', created_table_id::bigint,
    'name', relname,
    'renamed_columns', renamed_columns::jsonb,
    'pkey_column_attnum', msar.get_pk_column(created_table_id)
  ) FROM pg_catalog.pg_class WHERE oid = created_table_id;
END;
$_$;


--
-- Name: add_month_to_vector(point, date); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.add_month_to_vector(point_ point, date_ date) RETURNS point
    LANGUAGE sql STRICT
    AS $$/*
Add the month, converted to a vector on unit circle, to the vector in the first argument.

We add a date to a point by
- Exracting the month and converting the result to degrees.
- converting the degrees to a point on the unit circle.
- adding that point to the point given in the first argument.

Args:
  point_: A point representing a vector.
  date_: A date_ that is converted to a vector and added to the vector represented by point_.

Returns:
  point that stores the resultant vector after the addition.
*/
WITH t(degrees) AS (SELECT msar.month_to_degrees(date_))
SELECT point_ + point(sind(degrees), cosd(degrees)) FROM t;
$$;


--
-- Name: add_pkey_column(regclass, msar.pkey_kind, boolean, text); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.add_pkey_column(tab_id regclass, pkey_type msar.pkey_kind, drop_old_pkey_col boolean DEFAULT false, col_name text DEFAULT 'id'::text) RETURNS integer
    LANGUAGE plpgsql
    AS $$/*
Add a primary key column with a predefined default to a table.

Any name collisions for the column are resolved automatically, with this column's name deferring to
the original column names in the table.

Args:
  tab_id: This is the OID or name of the table to which we'll add the primary key column.
  pkey_type: The "type" of the pkey column. 'UUIDv4' means a `uuid` column using the
             `gen_random_uuid()` function for its default values. 'IDENTITY' means an `integer`
             using `GENERATED BY DEFAULT AS IDENTITY` for default values.
  drop_old_pkey_col: Whether we should drop the current primary key column during this operation.
  col_name: This optional value will set the name of the column.
*/
BEGIN
  IF drop_old_pkey_col THEN
    PERFORM msar.drop_columns(tab_id, msar.get_pk_column(tab_id));
  END IF;
  PERFORM msar.drop_constraint(tab_id, oid)
    FROM pg_catalog.pg_constraint WHERE conrelid=tab_id AND contype='p';
  EXECUTE format(
    'ALTER TABLE %I.%I ADD COLUMN %I %s;',
    msar.get_relation_schema_name(tab_id),
    msar.get_relation_name(tab_id),
    msar.build_unique_column_name(tab_id, col_name),
    CASE pkey_type
      WHEN 'IDENTITY' THEN 'integer PRIMARY KEY GENERATED BY DEFAULT AS IDENTITY'
      WHEN 'UUIDv4' THEN 'uuid PRIMARY KEY DEFAULT gen_random_uuid()'
    END
  );
  RETURN msar.get_pk_column(tab_id);
END;
$$;


--
-- Name: add_record_to_table(oid, jsonb, boolean, jsonb); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.add_record_to_table(tab_id oid, rec_def jsonb, return_record_summaries boolean DEFAULT false, table_record_summary_templates jsonb DEFAULT NULL::jsonb) RETURNS jsonb
    LANGUAGE plpgsql
    AS $_$/*
Add a record to a table.

Args:
  tab_id: The OID of the table whose record we'll delete.
  rec_def: A JSON object defining the record.

The `rec_def` object's form is defined by the record being inserted.  It should have keys
corresponding to the attnums of desired columns and values corresponding to values we should
insert.

*/
DECLARE
  rec_created_id text;
  rec_created jsonb;
BEGIN
  EXECUTE format(
    $q$
    WITH insert_cte AS (%1$s RETURNING %2$I)
    SELECT *
    FROM insert_cte
    $q$,
    /* %1 */ msar.build_single_insert_expr(tab_id, rec_def),
    /* %2 */ msar.get_column_name(tab_id, msar.get_pk_column(tab_id))
  ) INTO rec_created_id;
  rec_created := msar.get_record_from_table(
    tab_id,
    rec_created_id,
    null,
    return_record_summaries,
    table_record_summary_templates
  );
  RETURN jsonb_build_object(
    'results', rec_created -> 'results',
    'record_summaries', rec_created -> 'record_summaries',
    'linked_record_summaries', rec_created -> 'linked_record_summaries'
  );
END;
$_$;


--
-- Name: add_temp_table(text, __msar.col_def[]); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.add_temp_table(tab_name text, col_defs __msar.col_def[]) RETURNS text
    LANGUAGE plpgsql
    AS $$/*
Add a temporary table, returning the command executed.

Args:
  tab_name: An unqualified name for the table to be added.
  col_defs: An array of __msar.col_def defining the column set of the new table.
*/
DECLARE
  tmp_tab_sql text;
BEGIN
  WITH col_cte AS (
    SELECT string_agg(__msar.build_col_def_text(col), ', ') AS table_columns
    FROM unnest(col_defs) AS col
  )
  SELECT format(
    'CREATE TEMPORARY TABLE %I (%s)',
    tab_name,
    table_columns
  ) INTO tmp_tab_sql
  FROM col_cte;
  EXECUTE tmp_tab_sql;
  RETURN tmp_tab_sql;
END;
$$;


--
-- Name: add_time_to_vector(point, time without time zone); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.add_time_to_vector(point_ point, time_ time without time zone) RETURNS point
    LANGUAGE sql STRICT
    AS $$/*
Add the given time, converted to a vector on unit circle, to the vector given in first argument.

We add a time to a point by
- converting the time to a point on the unit circle.
- adding that point to the point given in the first argument.

Args:
  point_: A point representing a vector.
  time_: A time that is converted to a vector and added to the vector represented by point_.

Returns:
  point that stores the resultant vector after the addition.
*/
WITH t(degrees) AS (SELECT msar.time_to_degrees(time_))
SELECT point_ + point(sind(degrees), cosd(degrees)) FROM t;
$$;


--
-- Name: alter_columns(oid, jsonb); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.alter_columns(tab_id oid, col_alters jsonb) RETURNS integer[]
    LANGUAGE plpgsql STRICT
    AS $$/*
Alter columns of the given table in bulk, returning the IDs of the columns so altered.

Args:
  tab_id: The OID of the table whose columns we'll alter.
  col_alters: a JSONB describing the alterations to make.

The col_alters JSONB should have the form:
[
  {
    "attnum": <int>,
    "type": <obj> (optional),
    "default": <any> (optional),
    "not_null": <bool> (optional),
    "delete": <bool> (optional),
    "name": <str> (optional),
  },
  {
    ...
  },
  ...
]

Note that for all alterations, we create and execute separate SQL queries rather than combining them
into a giant SQL statement. This has the benefit of providing better error messages(for users)
and better code readability(for us) at the cost of a minor performance hit.
*/
DECLARE
  col RECORD;
  return_attnum_arr integer[];
  is_default_dynamic boolean;
BEGIN
  FOR col IN
    SELECT
      (col_alter_obj ->> 'attnum')::smallint AS attnum,
      (col_alter_obj -> 'not_null')::boolean AS not_null,
      (col_alter_obj ->> 'name')::text AS new_name,
      (col_alter_obj -> 'delete')::boolean AS delete_,
      msar.build_type_text_complete(col_alter_obj -> 'type', format_type(pga.atttypid, null)) AS new_type,
      COALESCE((col_alter_obj -> 'cast_options')::jsonb, '{}'::jsonb) AS cast_options,
      pg_catalog.pg_get_expr(adbin, tab_id) AS old_default,
      col_alter_obj -> 'default' AS new_default,

      col_alter_obj->>'description' AS comment_,
      __msar.jsonb_key_exists(col_alter_obj, 'description') AS has_comment

    FROM jsonb_array_elements(col_alters) AS x(col_alter_obj)
      INNER JOIN pg_catalog.pg_attribute AS pga ON pga.attnum=(x.col_alter_obj ->> 'attnum')::smallint AND pga.attrelid=tab_id
      LEFT JOIN pg_catalog.pg_attrdef AS pgat ON pgat.adnum=(x.col_alter_obj ->> 'attnum')::smallint AND pgat.adrelid=tab_id
    WHERE NOT msar.is_mathesar_id_column(tab_id, (x.col_alter_obj ->> 'attnum')::integer)
  LOOP
    PERFORM msar.set_not_null(tab_id, col.attnum, col.not_null);
    PERFORM msar.rename_column(tab_id, col.attnum, col.new_name);

    IF col.delete_ THEN
      PERFORM msar.drop_columns(tab_id, col.attnum);
    END IF;

    IF col.has_comment THEN
      PERFORM msar.comment_on_column(tab_id, col.attnum, col.comment_);
    END IF;

    -- is_default_possibly_dynamic check must happen before we drop the default.
    is_default_dynamic := msar.is_default_possibly_dynamic(tab_id, col.attnum);

    IF col.new_type IS NOT NULL OR jsonb_typeof(col.new_default)='null' THEN
      PERFORM msar.drop_col_default(tab_id, col.attnum);
    END IF;
    PERFORM msar.retype_column(tab_id, col.attnum, col.new_type, col.cast_options);
    IF col.new_default #>> '{}' IS NOT NULL THEN
      -- set new default
      PERFORM msar.set_col_default(tab_id, col.attnum, col.new_default #>> '{}');
    ELSEIF (col.new_default IS NULL OR jsonb_typeof(col.new_default)<>'null') AND col.new_type IS NOT NULL THEN
      -- preserve old default
      -- when a new_default is absent and col is retyped with a new_type.
      -- Note: We don't want to preserve old default for jsonb_typeof(col.new_default)='null'
      -- as we consider it as an intent to drop the default.
      PERFORM msar.set_old_col_default(tab_id, col.attnum, col.old_default, col.new_type, is_default_dynamic, col.cast_options);
    END IF;

    -- PG13 doesn't allow concat b/w integer[] and smallint need to typecast
    return_attnum_arr := return_attnum_arr || col.attnum::integer;
  END LOOP;
  RETURN return_attnum_arr; -- do we really need this??
END;
$$;


--
-- Name: alter_table(oid, jsonb); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.alter_table(tab_id oid, tab_alters jsonb) RETURNS text
    LANGUAGE plpgsql STRICT
    AS $$/*
Alter the name, description, or columns of a table, returning name of the altered table.

Args:
  tab_id: The OID of the table whose columns we'll alter.
  tab_alters: a JSONB describing the alterations to make.

  The tab_alters should have the form:
  {
    "name": <str>,
    "description": <str>
    "columns": <col_alters>,
  }
*/
DECLARE
  new_tab_name text;
  col_alters jsonb;
BEGIN
  new_tab_name := tab_alters->>'name';
  col_alters := tab_alters->'columns';
  PERFORM msar.rename_table(tab_id, new_tab_name);
  PERFORM CASE WHEN tab_alters ? 'description'
  THEN msar.comment_on_table(tab_id, tab_alters->>'description') END;
  PERFORM msar.alter_columns(tab_id, col_alters);
  RETURN __msar.get_qualified_relation_name_or_null(tab_id);
END;
$$;


--
-- Name: auto_generate_record_summary_template(oid); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.auto_generate_record_summary_template(tab_id oid) RETURNS jsonb
    LANGUAGE sql STABLE STRICT
    AS $$/*
  Given a table OID, this function generates a record summary template for the table. The template
  is generated by picking the best column to use for the record summary and wrapping it in an array.

  Args:
    tab_id: The OID of the table for which to generate a record summary template.

  Return value:
    A JSON array that represents the record summary template as described in
      msar.build_record_summary_query_from_template. The array contains a single element which is an
      array of column attnums. The column attnum is the best column to use for the record summary.
*/
SELECT jsonb_build_array(jsonb_build_array(msar.get_default_summary_column(tab_id)));
$$;


--
-- Name: build_all_columns_expr(regclass); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.build_all_columns_expr(tab_id regclass) RETURNS text
    LANGUAGE sql STABLE STRICT
    AS $_$/*
*/
SELECT string_agg(
  format(
    '%1$I.%2$I.%3$I AS %3$I',
    msar.get_relation_schema_name(tab_id),
    msar.get_relation_name(tab_id),
    attname
  ), ', '
)
FROM pg_catalog.pg_attribute
WHERE
  attrelid = tab_id
  AND attnum > 0
  AND NOT attisdropped;
$_$;


--
-- Name: build_cast_expr(regclass, smallint, regtype); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.build_cast_expr(tab_id regclass, col_id smallint, typ_id regtype) RETURNS text
    LANGUAGE sql STRICT
    AS $$/*
Build an expression for casting a column in Mathesar, returning the text of that expression.

We throw an error in cases where the casting function doesn't exist. This is assumed to be an error
the user should know about.

Args:
  tab_id: The OID of the table whose column we're casting.
  col_id: The attnum of the column in the table.
  typ_id: The OID of the type we will cast to.
*/
SELECT msar.get_cast_function_name(typ_id)
  || '('
  || format('%I', msar.get_column_name(tab_id, col_id))
  || ')';
$$;


--
-- Name: build_cast_expr(text, text, jsonb); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.build_cast_expr(val text, type_ text, cast_options jsonb) RETURNS text
    LANGUAGE sql STRICT
    AS $$/*
Build an expression for casting a column in Mathesar, returning the text of that expression.

Args:
  val: This is quite general, and isn't sanitized in any way. It can be either a literal or a column
       identifier, since we want to be able to produce a casting expression in either case.
  type_: This type name string must cast properly to a regtype.
  cast_options: Suggestions to be used while type casting.
*/
SELECT msar.get_cast_function_name(type_::regtype) || '(' ||
CONCAT_WS(', ',
  val,
  CASE WHEN NULLIF(cast_options, '{}'::jsonb) IS NOT NULL THEN
    CASE type_::regtype
      WHEN 'numeric'::regtype THEN
        CONCAT_WS(', ',
          'group_sep =>' || quote_literal(cast_options ->> 'group_sep') || '::"char"',
          'decimal_p =>' || quote_literal(cast_options ->> 'decimal_p') || '::"char"'
        )
      WHEN 'mathesar_types.mathesar_money'::regtype THEN
        CONCAT_WS(', ',
          'group_sep =>' || quote_literal(cast_options ->> 'group_sep') || '::"char"',
          'decimal_p =>' || quote_literal(cast_options ->> 'decimal_p') || '::"char"',
          'curr_pref =>' || quote_literal(COALESCE(cast_options ->> 'curr_pref', '')) || '::text',
          'curr_suff =>' || quote_literal(COALESCE(cast_options ->> 'curr_suff', '')) || '::text'
        )
    END
  END
) || ')'
$$;


--
-- Name: build_column_expr(text, jsonb); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.build_column_expr(tab_name text, columns jsonb) RETURNS text
    LANGUAGE sql STABLE STRICT
    AS $$/*
Build an SQL select-target expression of columns from the argument.
This is meant to work together with output of functions like msar.get_selectable_columns.

Returns an expr in the form: msar.format_data("<column name>") as "<oid>", ...

Args:
  tab_name: The unqoted name of the table for namespacing.
  columns: The columns to build the expr for, in the following jsonb sample format:
           { "2": <name of column with oid 2>, "4": <name of column with oid 4> }

*/
SELECT string_agg(
  format(
    'msar.format_data(%I.%I) AS %I',
    tab_name,
    sel_column.value,
    sel_column.key
  ),
  ', '
)
FROM jsonb_each_text(columns) as sel_column;
$$;


--
-- Name: build_columns_expr(regclass, smallint[]); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.build_columns_expr(tab_id regclass, col_ids smallint[]) RETURNS text
    LANGUAGE sql STABLE STRICT
    AS $_$/*
*/
SELECT string_agg(
  format(
    '%1$I.%2$I.%3$I AS %3$I',
    msar.get_relation_schema_name(tab_id),
    msar.get_relation_name(tab_id),
    attname
  ), ', '
)
FROM pg_catalog.pg_attribute JOIN unnest(col_ids) x(a) ON attnum = x.a
WHERE
  attrelid = tab_id;
$_$;


--
-- Name: build_database_privilege_replace_expr(regrole, jsonb); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.build_database_privilege_replace_expr(rol_id regrole, privileges_ jsonb) RETURNS text
    LANGUAGE sql STABLE STRICT
    AS $_$
SELECT string_agg(
  format(
    concat(
      CASE WHEN privileges_ ? val THEN 'GRANT' ELSE 'REVOKE' END,
      ' %1$s ON DATABASE %2$I ',
      CASE WHEN privileges_ ? val THEN 'TO' ELSE 'FROM' END,
      ' %3$I'
    ),
    val,
    pg_catalog.current_database(),
    msar.get_role_name(rol_id)
  ),
  E';\n'
) || E';\n'
FROM unnest(ARRAY['CONNECT', 'CREATE', 'TEMPORARY']) as x(val);
$_$;


--
-- Name: build_empty_record_summary_query(); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.build_empty_record_summary_query() RETURNS text
    LANGUAGE sql IMMUTABLE PARALLEL SAFE
    AS $_$/*
  Returns a stringified query structured consistently with a record summary query but which will
  yield no record summaries when run.
*/
  SELECT $q$ SELECT NULL AS key, NULL AS summary WHERE FALSE $q$;
$_$;


--
-- Name: build_expr(oid, jsonb); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.build_expr(rel_id oid, tree jsonb) RETURNS text
    LANGUAGE sql STABLE STRICT
    AS $$
SELECT CASE tree ->> 'type'
  WHEN 'literal' THEN format('%L', tree ->> 'value')
  WHEN 'attnum' THEN format('%I.%I', msar.get_relation_name(rel_id), msar.get_column_name(rel_id, (tree ->> 'value')::smallint))
  ELSE
    format(max(expr_template), VARIADIC array_agg(msar.build_expr(rel_id, inner_tree)))
END
FROM jsonb_array_elements(tree -> 'args') inner_tree, msar.expr_templates
WHERE tree ->> 'type' = expr_key
$$;


--
-- Name: build_grant_membership_expr(regrole, oid[]); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.build_grant_membership_expr(parent_rol_id regrole, g_roles oid[]) RETURNS text
    LANGUAGE sql STABLE STRICT
    AS $_$
SELECT string_agg(
  format(
    'GRANT %1$I TO %2$I',
    msar.get_role_name(parent_rol_id),
    msar.get_role_name(rol_id)
  ),
  E';\n'
) || E';\n'
FROM unnest(g_roles) as x(rol_id);
$_$;


--
-- Name: build_group_count_expr(oid, jsonb); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.build_group_count_expr(tab_id oid, group_ jsonb) RETURNS text
    LANGUAGE sql STABLE STRICT
    AS $$/*
Build an expression that adds a column with a count for each group.
*/
SELECT 'count(1) OVER (PARTITION BY ' || msar.build_grouping_columns_expr(tab_id, group_) || ')';
$$;


--
-- Name: build_group_id_expr(oid, jsonb); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.build_group_id_expr(tab_id oid, group_ jsonb) RETURNS text
    LANGUAGE sql STABLE STRICT
    AS $$/*
Build an expression to define an id value for each group.
*/
SELECT 'dense_rank() OVER (ORDER BY ' || msar.build_grouping_columns_expr(tab_id, group_) || ')';
$$;


--
-- Name: build_grouping_columns_expr(oid, jsonb); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.build_grouping_columns_expr(tab_id oid, group_ jsonb) RETURNS text
    LANGUAGE sql STABLE STRICT
    AS $$/*
Build a column expression for use in grouping window functions.

Args:
  tab_id: The OID of the table whose records we're grouping
  group_ A grouping definition.

The group_ object should have the form
    {
      "columns": [<int>, <int>, ...]
      "preproc": [<str>, <str>, ...]
    }

The items in the preproc array should be keys appearing in the
`expr_templates` table. The corresponding column will be wrapped
in the preproc function before grouping.
*/
SELECT string_agg(
  COALESCE(
    format(
      expr_template,
      quote_ident(msar.get_relation_name(tab_id))
      || '.' ||
      quote_ident(msar.get_column_name(tab_id, col_id::smallint))
    ),
    quote_ident(msar.get_relation_name(tab_id))
    || '.' ||
    quote_ident(msar.get_column_name(tab_id, col_id::smallint))
  ), ', ' ORDER BY ordinality
)
FROM msar.expr_templates RIGHT JOIN ROWS FROM(
  jsonb_array_elements_text(group_ -> 'columns'),
  jsonb_array_elements_text(group_ -> 'preproc')
) WITH ORDINALITY AS x(col_id, preproc) ON expr_key = preproc
WHERE has_column_privilege(tab_id, col_id::smallint, 'SELECT');
$$;


--
-- Name: build_grouping_expr(oid, jsonb); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.build_grouping_expr(tab_id oid, group_ jsonb) RETURNS text
    LANGUAGE sql STABLE
    AS $$/*
Build an expression composed of an id and count for each group.

A group is defined by distinct combinations of the (potentially transformed by preproc functions)
columns passed in `group_`.
*/
SELECT concat(
  COALESCE(msar.build_group_id_expr(tab_id, group_), 'NULL'), ' AS __mathesar_gid, ',
  COALESCE(msar.build_group_count_expr(tab_id, group_), 'NULL'), ' AS __mathesar_gcount'
);
$$;


--
-- Name: build_grouping_results_jsonb_expr(oid, text, jsonb); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.build_grouping_results_jsonb_expr(tab_id oid, cte_name text, group_ jsonb) RETURNS text
    LANGUAGE sql STABLE STRICT
    AS $_$/*
Build an SQL expresson string that, when added to the record listing query, produces a JSON array
with the groups resulting from the request.
*/
SELECT format(
  $gj$
  jsonb_build_object(
    'columns', %2$L::jsonb,
    'preproc', %3$L::jsonb,
    'groups', jsonb_agg(
      DISTINCT jsonb_build_object(
        'id', %1$I.id,
        'count', %1$I.count,
        'results_eq', %1$I.results_eq,
        'result_indices', %1$I.result_indices
      )
    )
  )
  $gj$,
  cte_name,
  group_ ->> 'columns',
  group_ ->> 'preproc'
)
$_$;


--
-- Name: build_groups_cte_expr(oid, text, text, jsonb); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.build_groups_cte_expr(tab_id oid, eq_cte_name text, ranked_cte_name text, group_ jsonb) RETURNS text
    LANGUAGE sql STABLE STRICT
    AS $_$/*
*/
SELECT format(
  $gj$
    %1$I.__mathesar_gid AS id,
    __mathesar_gcount AS count,
    to_jsonb(%1$I) - '__mathesar_gid' AS results_eq,
    jsonb_agg( DISTINCT __mathesar_result_idx) AS result_indices
  FROM %1$I LEFT JOIN %2$I AS rcn ON %1$I.__mathesar_gid = rcn.__mathesar_gid
  GROUP BY id, count, results_eq
  $gj$,
  eq_cte_name,
  ranked_cte_name
);
$_$;


--
-- Name: build_insert_lookup_table(jsonb, jsonb); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.build_insert_lookup_table(field_info_list jsonb, values_ jsonb) RETURNS TABLE(table_name text, column_names text, values_ text, cte_name text, from_cte_name text)
    LANGUAGE sql STABLE STRICT
    AS $$/*
Returns a lookup table for given field_info_list and values_

Example: Inserting into Items while creating a new book entry, adding a new Title,
creating a new entry for Author, picking a Publisher.
           table_name           |                  column_names                   |                values_                 | cte_name | from_cte_name
--------------------------------+-------------------------------------------------+----------------------------------------+----------+---------------
 "Library Management"."Authors" | "First Name", "Last Name"                       | 'Jerome K.', 'Jerome                   | k1_cte   |
 "Library Management"."Books"   | "Title", "Author", "Publisher"                  | 'Three men in a Boat', k1_cte.id, '12' | k0_cte   | k1_cte
 "Library Management"."Items"   | "Acquisition Date", "Acquisition Price", "Book" | '2025-10-09', '69.69', k0_cte.id       |          | k0_cte

Calling msar.form_insert() on this table would generate the following SQL:

WITH k1_cte AS (
  INSERT INTO "Library Management"."Authors"("First Name", "Last Name") SELECT 'Jerome K.', 'Jerome' RETURNING *
), k0_cte AS (
  INSERT INTO "Library Management"."Books"("Title", "Author", "Publisher") SELECT 'Three men in a Boat', k1_cte.id, '12' FROM k1_cte RETURNING *
) INSERT INTO "Library Management"."Items"("Acquisition Date", "Acquisition Price", "Book") SELECT '2025-10-09', '69.69', k0_cte.id FROM k0_cte RETURNING *
*/
WITH cte AS (
  SELECT
    pga.attname::name AS column_name,
    fields.table_oid::bigint AS table_oid,
    fields.depth::integer AS depth,
    CASE
      WHEN vals.value::jsonb->>'type' = 'create' THEN concat(quote_ident(concat(fields.key::text, '_cte')), '.', quote_ident(ref_attr.attname))
      WHEN vals.value::jsonb->>'type' = 'pick' THEN quote_nullable(vals.value::jsonb->>'value')
      ELSE quote_nullable(vals.value::jsonb #>> '{}')
    END AS value,
    CASE
      WHEN fields.parent_key IS NOT NULL THEN quote_ident(concat(fields.parent_key::text, '_cte'))
      ELSE NULL
    END AS cte_name,
    CASE
      WHEN vals.value::jsonb->>'type' = 'create' THEN quote_ident(concat(fields.key::text, '_cte'))
      ELSE NULL
    END AS from_cte_name
  FROM jsonb_to_recordset(field_info_list) AS fields(
    key text,
    parent_key text,
    column_attnum smallint,
    table_oid bigint,
    depth integer)
  INNER JOIN jsonb_each(values_) AS vals ON vals.key = fields.key
  LEFT JOIN pg_catalog.pg_attribute pga ON pga.attnum = fields.column_attnum AND pga.attrelid = fields.table_oid

  LEFT JOIN pg_catalog.pg_constraint pgc
    ON fields.column_attnum = ANY(pgc.conkey)
    AND pgc.conrelid = fields.table_oid
    AND pgc.contype = 'f'
  LEFT JOIN unnest(pgc.conkey) WITH ORDINALITY AS ck(attnum, ord) ON ck.attnum = fields.column_attnum
  LEFT JOIN unnest(pgc.confkey) WITH ORDINALITY AS fk(attnum, ord) ON fk.ord = ck.ord
  LEFT JOIN pg_catalog.pg_attribute ref_attr ON ref_attr.attrelid = pgc.confrelid AND ref_attr.attnum = fk.attnum
), multi_fks_cte AS (
  SELECT msar.raise_exception(
    'Inserting into a column with foreign key constraints referencing multiple columns is currently unsupported.'
  )
  FROM cte GROUP BY column_name, from_cte_name HAVING count(*) > 1
)
SELECT
  __msar.get_qualified_relation_name(table_oid) AS table_name,
  string_agg(quote_ident(column_name), ', ') AS column_names,
  string_agg(value, ', ') AS values_,
  cte_name,
  string_agg(from_cte_name, ', ') AS from_cte_name
FROM cte
CROSS JOIN (SELECT COUNT(*) FROM multi_fks_cte) AS force_multi_fks_cte_execution
GROUP BY table_oid, cte_name, depth ORDER BY depth DESC;
$$;


--
-- Name: build_join_expr(jsonb); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.build_join_expr(join_path jsonb) RETURNS text
    LANGUAGE sql STRICT
    AS $$/* 
Returns a left join sql expr for a given join path.

Note: This doesn't handle aliasing.
  So, join_paths containing the same table to be joined more than once would through errors.
*/
  WITH cte AS (
    SELECT
      msar.get_relation_name((joins->0->>0)::oid) AS left_tab_name,
      msar.get_column_name((joins->0->>0)::oid, (joins->0->>1)::int) AS left_col_name,
      msar.get_relation_schema_name((joins->1->>0)::oid) AS right_tab_sch_name,
      msar.get_relation_name((joins->1->>0)::oid) AS right_tab_name,
      msar.get_column_name((joins->1->>0)::oid, (joins->1->>1)::int) AS right_col_name
    FROM jsonb_array_elements(join_path) AS joins
  ), join_expr_cte AS (
    SELECT format(
      'LEFT JOIN %I.%I ON %I.%I = %I.%I',
      cte.right_tab_sch_name,
      cte.right_tab_name,
      cte.left_tab_name,
      cte.left_col_name,
      cte.right_tab_name,
      cte.right_col_name
    ) AS join_expr FROM cte
  ) SELECT string_agg(join_expr_cte.join_expr , E'\n') FROM join_expr_cte
$$;


--
-- Name: build_joined_columns_summaries_ctes(text, jsonb, jsonb); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.build_joined_columns_summaries_ctes(results_cte_name text, joined_columns jsonb, table_record_summary_templates jsonb DEFAULT NULL::jsonb) RETURNS text
    LANGUAGE sql STABLE
    AS $_$/*
Build an SQL text expression defining a sequence of CTEs that give summaries for joined columns.

Args:
  results_cte_name: The name of the results cte.
  joined_columns: A jsonb list defining columns joined via a simple many-to-many linkage.
    See msar.get_joined_columns_expr_json for more details.
  table_record_summary_templates: (optional) A JSON object that maps table OIDs to record summary
    templates.
*/
SELECT
  ', ' ||
  NULLIF(
    string_agg(
      format(
        $q$ %1$I AS (
          %3$s
          RIGHT JOIN %4$I ON to_jsonb(base.id) <@ (%4$I.%2$I->'result')
        )$q$, /* This join helps us filter distinct record summaries based on the result
        of aggregated records of the joined columns */
        alias || '_cte',
        alias,
        msar.build_record_summary_query_for_table(
          (join_path->-1->-1->>0)::oid,
          (join_path->-1->-1->>1)::smallint,
          table_record_summary_templates
        ),
        results_cte_name
      ),
      ', '
    ),
    ''
  )
FROM jsonb_to_recordset(joined_columns) AS (
  alias text,
  join_path jsonb
)
$_$;


--
-- Name: build_joined_columns_summaries_expr(jsonb); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.build_joined_columns_summaries_expr(joined_columns jsonb) RETURNS text
    LANGUAGE sql STABLE
    AS $_$/*
Returns a SELECT SQL expr for aggregating record summaries
from the ctes generated via msar.build_joined_columns_summaries_ctes.

Args:
  joined_columns: A jsonb list defining columns joined via a simple many-to-many linkage.
    See msar.get_joined_columns_expr_json for more details.
*/
SELECT 'SELECT '
|| string_agg(
  format(
    $j$
      COALESCE(
        jsonb_object_agg(
          %1$I.key, %1$I.summary
        ) FILTER (WHERE %1$I.key IS NOT NULL), '{}'::jsonb
      ) AS %2$I
    $j$,
    alias || '_cte',
    alias
  ), ', '
)
|| ' FROM ' || string_agg(format('%I', alias || '_cte'), ', ')
FROM jsonb_to_recordset(joined_columns) AS (
  alias text,
  join_path jsonb
)
$_$;


--
-- Name: build_linked_record_summaries_ctes(oid, jsonb); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.build_linked_record_summaries_ctes(tab_id oid, table_record_summary_templates jsonb DEFAULT NULL::jsonb) RETURNS text
    LANGUAGE sql STABLE
    AS $_$/*
Build an SQL text expression defining a sequence of CTEs that give summaries for linked records.

Args:
  tab_id: The table for whose fkey values' linked records we'll get summaries.
*/
SELECT
  ', ' ||
  NULLIF(
    string_agg(
      format(
        $q$summary_cte_%1$s AS (%2$s)$q$,
        conkey,
        msar.build_record_summary_query_for_table(
          target_oid,
          confkey,
          table_record_summary_templates
        )
      ),
      ', '
    ),
    ''
  )
FROM msar.get_fkey_map_table(tab_id)
$_$;


--
-- Name: build_order_by_expr(oid, jsonb); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.build_order_by_expr(tab_id oid, order_ jsonb) RETURNS text
    LANGUAGE sql STABLE
    AS $$/*
Build an ORDER BY expression for the given table and order JSON.

The ORDER BY expression will refer to columns by their attnum. This is designed to work together
with `msar.build_selectable_column_expr`. It will only use the columns to which the user has access.
Finally, this function will append either a primary key, or all columns to the produced ORDER BY so
the resulting ordering is totally defined (i.e., deterministic).

Args:
  tab_id: The OID of the table whose columns we'll order by.
  order_: A JSONB array defining any desired ordering of columns.
*/
SELECT 'ORDER BY ' || msar.build_total_order_expr(tab_id, order_)
$$;


--
-- Name: build_record_list_query_components_with_ctes(oid, integer, integer, jsonb, jsonb, jsonb, jsonb); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.build_record_list_query_components_with_ctes(tab_id oid, limit_ integer, offset_ integer, order_ jsonb, filter_ jsonb, group_ jsonb, joined_columns jsonb) RETURNS jsonb
    LANGUAGE plpgsql STABLE
    AS $_$/*
  Constructs the components necessary for generating enriched query results,
  including expressions, clauses, selectable_column list, and CTEs, for a table.

  Args:
    tab_id: The OID of the table whose records we'll get
    limit_: The maximum number of rows we'll return
    offset_: The number of rows to skip before returning records from following rows
    order_: An array of ordering definition objects
    filter_: An array of filter definition objects
    group_: An array of group definition objects
    joined_columns: (optional) A jsonb list defining columns joined via a simple many-to-many linkage.
      See msar.get_joined_columns_expr_json for more details.

  Behavior:
    Fetches metadata about the table (selectable_column list, schema name, table name etc.,)
    Constructs expressions and SQL snippets (SELECT, WHERE, GROUP BY, etc.,)
    Generates two SQL queries:
      1. A query for paginated results (`results_cte_query`).
      2. A query to count the total matching rows (`count_cte_query`).
    Returns a jsonb object combining metadata, the expressions, and the generated SQL queries.
*/
DECLARE
  expr_object jsonb;
  joinable_expr_object jsonb;
  results_cte_query text;
  count_cte_query text;
BEGIN
  SELECT jsonb_build_object(
    'relation_name', msar.get_relation_name(tab_id),
    'relation_schema_name', msar.get_relation_schema_name(tab_id),
    'selectable_columns_expr', msar.build_selectable_column_expr(tab_id),
    'grouping_expr', msar.build_grouping_expr(tab_id, group_),
    'order_by_expr', msar.build_order_by_expr(tab_id, order_),
    'where_clause', msar.build_where_clause(tab_id, filter_)
  ) INTO expr_object;

  joinable_expr_object :=
    CASE
      WHEN joined_columns IS NOT NULL THEN
        msar.get_joined_columns_expr_json(joined_columns)
      ELSE NULL
    END;

  SELECT format(
    $q$SELECT %1$s, %2$s FROM %3$I.%4$I %5$s %6$s %7$s %8$s LIMIT %9$L OFFSET %10$L$q$,
    /* %1 */ CONCAT_WS(
               ', ',
               COALESCE(expr_object ->> 'selectable_columns_expr', 'NULL'),
               joinable_expr_object ->> 'selectable_joined_columns_expr'
             ),
    /* %2 */ COALESCE(expr_object ->> 'grouping_expr', 'NULL'),
    /* %3 */ expr_object ->> 'relation_schema_name',
    /* %4 */ expr_object ->> 'relation_name',
    /* %5 */ joinable_expr_object ->> 'join_sql_expr',
    /* %6 */ expr_object ->> 'where_clause',
    /* %7 */ joinable_expr_object ->> 'join_group_by_expr',
    /* %8 */ expr_object ->> 'order_by_expr',
    /* %9 */ limit_,
    /* %10 */ offset_
  ) INTO results_cte_query;

  SELECT format(
    $q$SELECT count(1) AS count FROM %1$I.%2$I %3$s$q$,
    expr_object ->> 'relation_schema_name',
    expr_object ->> 'relation_name',
    expr_object ->> 'where_clause'
  ) INTO count_cte_query;

  RETURN expr_object || jsonb_build_object(
    'results_cte_query', results_cte_query,
    'count_cte_query', count_cte_query
  );
END
$_$;


--
-- Name: build_record_summary_query_for_table(oid, smallint, jsonb); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.build_record_summary_query_for_table(tab_id oid, key_col_id smallint DEFAULT NULL::smallint, table_record_summary_templates jsonb DEFAULT '{}'::jsonb) RETURNS text
    LANGUAGE sql STABLE
    AS $$/*
Return text for an SQL query that will summarize records from a table.

Args:
  tab_id: the OID of the table for which we're getting summaries.
  key_col_id: (optional) This is a column attnum in the table. When given, this column will be used
    as the key in the summary. If not given, the table's PK column will be used.
  table_record_summary_templates: (optional) A JSON object that maps table OIDs to record summary
    templates.
*/
SELECT msar.build_record_summary_query_from_template(
  tab_id,
  COALESCE(key_col_id, msar.get_selectable_pkey_attnum(tab_id)),
  COALESCE(
    NULLIF(table_record_summary_templates -> tab_id::text, 'null'::jsonb),
    msar.auto_generate_record_summary_template(tab_id)
  )
);
$$;


--
-- Name: build_record_summary_query_from_template(oid, smallint, jsonb); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.build_record_summary_query_from_template(tab_id oid, key_col_id smallint, template jsonb) RETURNS text
    LANGUAGE plpgsql STABLE
    AS $$/*
  Given a table OID and a record summary template, this function returns a query that can be used to
  generate record summaries for the table.

  Args:
    tab_id: The OID of the table for which to generate a record summary query.
    template: A JSON array that represents the record summary template (described in detail below).

  Example template:

    [
      "#",
      [1],
      " - ",
      [2, 5],
      " - ",
      [2, 5, 10]
    ]

  A string entry in the template represents static text to be included in the record summary
  verbatim.

  An array entry in the template represents a reference to data. Each element in the array is a
  column attnum. The first column attnum refers to a column in the base table. If the array
  contains more than one column reference, it represents a chain of FK columns starting from
  the base table and ending with a non-FK column. This function follows the foreign keys to
  produce the joins. Multi-column FK constraints are not supported.

  Return value: a stringified query which produces a result set matching the structure described
    in the return value of msar.get_record_summaries_via_query.
*/
DECLARE
  base_alias CONSTANT text := 'base';
  expr_parts text[] := ARRAY[]::text[];
  expr text;
  base_sch_name text := msar.get_relation_schema_name(tab_id);
  base_tab_name text := msar.get_relation_name(tab_id);
  base_key_col_name text := msar.get_column_name(tab_id, key_col_id);
  template_part jsonb;
  join_clauses text[] := ARRAY[]::text[];
  join_section text;
BEGIN
  IF key_col_id IS NULL THEN
    -- If we don't have a key column, then we can't generate a record summary query.
    RETURN msar.build_empty_record_summary_query();
  END IF;

  IF NOT pg_catalog.has_column_privilege(tab_id, key_col_id, 'SELECT') THEN
    -- If we don't have permission to select the key column, then we can't generate a record
    RETURN msar.build_empty_record_summary_query();
  END IF;

  IF jsonb_typeof(template) <> 'array' THEN
    RAISE EXCEPTION 'Record summary template must be a JSON array.';
  END IF;

  <<template_parts_loop>>
  FOR template_part IN SELECT jsonb_array_elements(template) LOOP
    DECLARE
      ref_chain smallint[] := msar.extract_smallints(template_part);
      ref_chain_length integer := array_length(ref_chain, 1);
      fk_col_id smallint;
      contextual_tab_id oid := tab_id;
      prev_alias text := base_alias;
      ref_col_id smallint;
      ref_col_name text;
    BEGIN
      -- Column reference template parts
      IF ref_chain_length > 0 THEN
        -- Except for the final ref_chain element, process all array elements as attnums of FK
        -- columns.
        FOREACH fk_col_id IN ARRAY ref_chain[1:ref_chain_length-1] LOOP
          DECLARE
            fk_col_name text;
            ref_tab_id oid;
            ref_sch_name text;
            ref_tab_name text;
            alias text;
            join_clause text;
          BEGIN
            IF NOT pg_catalog.has_column_privilege(contextual_tab_id, fk_col_id, 'SELECT') THEN
              -- Silently ignore FK columns that we don't have permissions to select.
              CONTINUE template_parts_loop;
            END IF;

            fk_col_name := msar.get_column_name(contextual_tab_id, fk_col_id);

            IF fk_col_name IS NULL THEN
              -- Silently ignore references to non-existing FK columns. This can happen if a column
              -- has been deleted.
              CONTINUE template_parts_loop;
            END IF;

            SELECT confrelid, confkey[1] INTO ref_tab_id, ref_col_id
            FROM pg_catalog.pg_constraint
            WHERE contype = 'f' AND conrelid = contextual_tab_id AND conkey = array[fk_col_id];

            IF ref_tab_id IS NULL THEN
              -- Silently ignore references to non-FK columns. This can happen if the constraint
              -- has been dropped.
              CONTINUE template_parts_loop;
            END IF;

            IF NOT pg_catalog.has_column_privilege(ref_tab_id, ref_col_id, 'SELECT') THEN
              -- Silently ignore FK columns which point to columns that we don't have permission to
              -- select.
              CONTINUE template_parts_loop;
            END IF;

            ref_tab_name := msar.get_relation_name(ref_tab_id);
            ref_sch_name := msar.get_relation_schema_name(ref_tab_id);
            ref_col_name := msar.get_column_name(ref_tab_id, ref_col_id);
            alias := concat(prev_alias, '_', fk_col_id);
            join_clause := concat(
              'LEFT JOIN ',
              quote_ident(ref_sch_name), '.', quote_ident(ref_tab_name),
              ' AS ', alias,
              ' ON ',
              alias, '.', quote_ident(ref_col_name),
              ' = ',
              prev_alias, '.', quote_ident(fk_col_name)
            );

            IF NOT join_clauses @> ARRAY[join_clause] THEN
              join_clauses := array_append(join_clauses, join_clause);
            END IF;
            prev_alias := alias;
            contextual_tab_id := ref_tab_id;
          END;
        END LOOP;

        ref_col_id := ref_chain[ref_chain_length];

        IF NOT pg_catalog.has_column_privilege(contextual_tab_id, ref_col_id, 'SELECT') THEN
          -- Silently ignore the final column reference if we don't have permission to select it.
          CONTINUE template_parts_loop;
        END IF;

        ref_col_name := msar.get_column_name(contextual_tab_id, ref_col_id);
        IF ref_col_name IS NOT NULL THEN
          expr_parts := array_append(
            expr_parts,
            concat(
              'COALESCE(msar.format_data(',
              prev_alias, '.', quote_ident(ref_col_name),
              E')::text, \'\')'
            )
          );
        END IF;

      -- String literal template parts
      ELSIF jsonb_typeof(template_part) = 'string' THEN
        expr_parts := array_append(expr_parts, quote_literal(template_part #>> '{}'));
      END IF;
    END;
  END LOOP;

  IF cardinality(expr_parts) = 0 THEN
    -- If the template didn't give us anything to render, then we show '?' as a fallback. This can
    -- happen if (e.g.) the template only contains a reference which is no longer valid due to a
    -- column being deleted.
    expr_parts := array_append(expr_parts, quote_literal('?'));
  END IF;

  join_section := CASE
    WHEN array_length(join_clauses, 1) = 0 THEN ''
    ELSE E'\n' || array_to_string(join_clauses, E'\n')
  END;

  expr := array_to_string(expr_parts, E'\n    || ');

  RETURN concat(
    E'SELECT \n',
    '  ', base_alias, '.', quote_ident(base_key_col_name), E' AS key, \n',
    '  ', expr, E' AS summary \n',
    'FROM ',
    quote_ident(base_sch_name), '.', quote_ident(base_tab_name),
    ' AS ', base_alias,
    join_section
  );
END;
$$;


--
-- Name: build_results_eq_cte_expr(oid, text, jsonb); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.build_results_eq_cte_expr(tab_id oid, cte_name text, group_ jsonb) RETURNS text
    LANGUAGE sql STABLE STRICT
    AS $_$
SELECT string_agg(
  format(
    '%1$s AS %2$I',
    COALESCE(
      format(expr_template, quote_ident(cte_name) || '.' || quote_ident(col_id)),
      quote_ident(cte_name) || '.' || quote_ident(col_id)
    ),
    col_id
  ),
  ', ' ORDER BY ordinality
) || ', __mathesar_gid FROM ' || quote_ident(cte_name)
|| ' GROUP BY __mathesar_gid, '
|| string_agg(
  format(
    '%1$I',
    col_id
  ),
  ', ' ORDER BY ordinality
)
FROM msar.expr_templates RIGHT JOIN ROWS FROM(
  jsonb_array_elements_text(group_ -> 'columns'),
  jsonb_array_elements_text(group_ -> 'preproc')
) WITH ORDINALITY AS x(col_id, preproc) ON expr_key = preproc
WHERE has_column_privilege(tab_id, col_id::smallint, 'SELECT');
$_$;


--
-- Name: build_results_jsonb_array_expr(text, text); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.build_results_jsonb_array_expr(cte_name text, order_by_expr text) RETURNS text
    LANGUAGE sql STABLE
    AS $_$/*
Build an SQL expresson string that, when added to the record listing query, produces a JSON array
with the records resulting from the request.
*/
SELECT format(
  $j$
    COALESCE(
      jsonb_agg(
        to_jsonb(%2$I) - %3$L - %4$L %1$s
      ), jsonb_build_array()
    )
  $j$,
  /* %1 */ order_by_expr,
  /* %2 */ cte_name,
  /* %3 */ '__mathesar_gid',
  /* %4 */ '__mathesar_gcount'
);
$_$;


--
-- Name: build_results_setof_jsonb_expr(text); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.build_results_setof_jsonb_expr(cte_name text) RETURNS text
    LANGUAGE sql STABLE
    AS $_$/*
Build an SQL expresson string that, when added to the record listing query, produces a setof jsonb
results with the records resulting from the request.
*/
SELECT format(
  'to_jsonb(%1$I) - %2$L - %3$L',
  /* %1 */ cte_name,
  /* %2 */ '__mathesar_gid',
  /* %3 */ '__mathesar_gcount'
);
$_$;


--
-- Name: build_revoke_membership_expr(regrole, oid[]); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.build_revoke_membership_expr(parent_rol_id regrole, r_roles oid[]) RETURNS text
    LANGUAGE sql STABLE STRICT
    AS $_$
SELECT string_agg(
  format(
    'REVOKE %1$I FROM %2$I',
    msar.get_role_name(parent_rol_id),
    msar.get_role_name(rol_id)
  ),
  E';\n'
) || E';\n'
FROM unnest(r_roles) as x(rol_id);
$_$;


--
-- Name: build_schema_privilege_replace_expr(regnamespace, regrole, jsonb); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.build_schema_privilege_replace_expr(sch_id regnamespace, rol_id regrole, privileges_ jsonb) RETURNS text
    LANGUAGE sql STABLE STRICT
    AS $_$
SELECT string_agg(
  format(
    concat(
      CASE WHEN privileges_ ? val THEN 'GRANT' ELSE 'REVOKE' END,
      ' %1$s ON SCHEMA %2$I ',
      CASE WHEN privileges_ ? val THEN 'TO' ELSE 'FROM' END,
      ' %3$I'
    ),
    val,
    msar.get_schema_name(sch_id),
    msar.get_role_name(rol_id)
  ),
  E';\n'
) || E';\n'
FROM unnest(ARRAY['USAGE', 'CREATE']) as x(val);
$_$;


--
-- Name: build_selectable_column_expr(oid); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.build_selectable_column_expr(tab_id oid) RETURNS text
    LANGUAGE sql STABLE STRICT
    AS $$/*
Build an SQL select-target expression of only columns to which the user has access.

Given columns with attnums 2, 3, and 4, and assuming the user has access only to columns 2 and 4,
this function will return an expression of the form:

msar.format_data("column_name") AS "2", msar.format_data("another_column_name") AS "4"

Args:
  tab_id: The OID of the table containing the columns to select.
*/
SELECT msar.build_column_expr(msar.get_relation_name(tab_id), msar.get_selectable_columns(tab_id));
$$;


--
-- Name: build_self_summary_json_expr(oid); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.build_self_summary_json_expr(tab_id oid) RETURNS text
    LANGUAGE sql STABLE STRICT
    AS $_$/*
*/
SELECT CASE WHEN quote_ident(msar.get_selectable_pkey_attnum(tab_id)::text) IS NOT NULL THEN
  $j$
  COALESCE(
    jsonb_object_agg(
      summary_cte_self.key, summary_cte_self.summary
    ) FILTER (WHERE summary_cte_self.key IS NOT NULL), '{}'::jsonb
  ) AS summary_self
  $j$
END;
$_$;


--
-- Name: build_single_insert_expr(oid, jsonb); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.build_single_insert_expr(tab_id oid, rec_def jsonb) RETURNS text
    LANGUAGE sql STABLE STRICT
    AS $$
SELECT CASE WHEN NULLIF(rec_def, '{}'::jsonb) IS NOT NULL THEN
  (
    SELECT
      format(
        'INSERT INTO %I.%I (%s) VALUES (%s)',
        msar.get_relation_schema_name(tab_id),
        msar.get_relation_name(tab_id),
        string_agg(format('%I', msar.get_column_name(tab_id, key::smallint)), ', '),
        string_agg(format('%L', value), ', ')
      )
    FROM jsonb_each_text(rec_def)
  )
ELSE
  format(
    'INSERT INTO %I.%I DEFAULT VALUES',
    msar.get_relation_schema_name(tab_id),
    msar.get_relation_name(tab_id)
  )
END;
$$;


--
-- Name: build_source_update_cte_join_condition_expr(regclass, smallint, smallint[], text, text); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.build_source_update_cte_join_condition_expr(target_tab_id regclass, target_join_col_id smallint, added_col_ids smallint[], update_target_cte_name text, insert_cte_name text) RETURNS text
    LANGUAGE sql STABLE STRICT
    AS $_$
SELECT 'ON ' || string_agg(
  format(
    '%1$I.%3$I IS NOT DISTINCT FROM %2$I.%3$I',
    update_target_cte_name,
    insert_cte_name,
    attname
  ), ' AND '
)
FROM
  pg_catalog.pg_attribute
  JOIN unnest(msar.get_other_column_ids(target_tab_id, added_col_ids || target_join_col_id)) x(a)
  ON attnum = x.a
WHERE
  attrelid = target_tab_id;
$_$;


--
-- Name: build_source_update_move_cols_equal_expr(regclass, smallint[], text); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.build_source_update_move_cols_equal_expr(source_tab_id regclass, move_col_ids smallint[], cte_name text) RETURNS text
    LANGUAGE sql STABLE STRICT
    AS $_$
SELECT string_agg(
  format(
    -- TODO should be IS NOT DISTINCT FROM
    '%1$I.%2$I.%3$I = %4$I.%3$I',
    msar.get_relation_schema_name(source_tab_id),
    msar.get_relation_name(source_tab_id),
    attname,
    cte_name
  ), ' AND '
)
FROM pg_catalog.pg_attribute JOIN unnest(move_col_ids) x(a) ON attnum = x.a
WHERE
  attrelid = source_tab_id;
$_$;


--
-- Name: build_summary_join_expr_for_table(oid, text); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.build_summary_join_expr_for_table(tab_id oid, cte_name text) RETURNS text
    LANGUAGE sql STABLE STRICT
    AS $_$/*
Build an SQL expression to join the summary CTEs to the main CTE along fkey values.

Args:
  tab_oid: The table defining the columns of the main CTE.
  cte_name: The name of the main CTE we'll join the summary CTEs to.
*/
WITH fkey_map_cte AS (SELECT * FROM msar.get_fkey_map_table(tab_id))
SELECT concat(
  format(E'\nLEFT JOIN summary_cte_self ON %1$I.', cte_name)
  || quote_ident(msar.get_selectable_pkey_attnum(tab_id)::text)
  || ' = summary_cte_self.key' ,
  string_agg(
    format(
      $j$
      LEFT JOIN summary_cte_%1$s ON %2$I.%1$I = summary_cte_%1$s.key$j$,
      conkey,
      cte_name
    ), ' '
  )
)
FROM fkey_map_cte;
$_$;


--
-- Name: build_summary_json_expr_for_table(oid); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.build_summary_json_expr_for_table(tab_id oid) RETURNS text
    LANGUAGE sql STABLE STRICT
    AS $_$/*
Build a JSON object with the results of summarizing linked records.

Args:
  tab_oid: The OID of the table for which we're getting linked record summaries.
*/
WITH fkey_map_cte AS (SELECT * FROM msar.get_fkey_map_table(tab_id))
SELECT string_agg(
  format(
    $j$
      COALESCE(
        jsonb_object_agg(
          summary_cte_%1$s.key, summary_cte_%1$s.summary
        ) FILTER (WHERE summary_cte_%1$s.key IS NOT NULL), '{}'::jsonb
      ) AS %1$I
    $j$,
    conkey
  ), ', '
)
FROM fkey_map_cte;
$_$;


--
-- Name: build_table_privilege_replace_expr(regclass, regrole, jsonb); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.build_table_privilege_replace_expr(tab_id regclass, rol_id regrole, privileges_ jsonb) RETURNS text
    LANGUAGE sql STABLE STRICT
    AS $_$
SELECT string_agg(
  format(
    concat(
      CASE WHEN privileges_ ? val THEN 'GRANT' ELSE 'REVOKE' END,
      ' %1$s ON TABLE %2$I.%3$I ',
      CASE WHEN privileges_ ? val THEN 'TO' ELSE 'FROM' END,
      ' %4$I'
    ),
    val,
    msar.get_relation_schema_name(tab_id),
    msar.get_relation_name(tab_id),
    msar.get_role_name(rol_id)
  ),
  E';\n'
) || E';\n'
FROM unnest(ARRAY['INSERT', 'SELECT', 'UPDATE', 'DELETE', 'TRUNCATE', 'REFERENCES', 'TRIGGER']) as x(val);
$_$;


--
-- Name: build_total_order_expr(oid, jsonb); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.build_total_order_expr(tab_id oid, order_ jsonb) RETURNS text
    LANGUAGE sql STABLE
    AS $$/*
Build a deterministic order expression for the given table and order JSON.
Args:
  tab_id: The OID of the table whose columns we'll order by.
  order_: A JSONB array defining any desired ordering of columns.
*/
SELECT string_agg(format('%I %s', attnum, msar.sanitize_direction(direction)), ', ')
FROM jsonb_to_recordset(
    COALESCE(
      COALESCE(order_, '[]'::jsonb) || msar.get_pkey_order(tab_id),
      COALESCE(order_, '[]'::jsonb) || msar.get_total_order(tab_id)
    )
)
  AS x(attnum smallint, direction text)
WHERE has_column_privilege(tab_id, attnum, 'SELECT');
$$;


--
-- Name: build_type_text(jsonb); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.build_type_text(typ_jsonb jsonb) RETURNS text
    LANGUAGE sql
    AS $$/*
Turns the given type-describing JSON into a proper string defining a type with arguments

The input JSON should be of the form
  {
    "id": <integer>
    "schema": <str>,
    "name": <str>,
    "modifier": <integer>,
    "options": {
      "length": <integer>,
      "precision": <integer>,
      "scale": <integer>
      "fields": <str>,
      "array": <boolean>
    }
  }

All fields are optional, and a null value as input returns 'text'
*/
SELECT COALESCE(
  -- First choice is the type specified by numeric IDs, since they're most reliable.
  format_type(
    (typ_jsonb ->> 'id')::integer,
    (typ_jsonb ->> 'modifier')::integer
  ),
  -- Second choice is the type specified by string IDs.
  __msar.get_formatted_base_type(
    COALESCE(
      __msar.build_qualified_name_sql(typ_jsonb ->> 'schema', typ_jsonb ->> 'name'),
      typ_jsonb ->> 'name',
      'text'  -- We fall back to 'text' when input is null or empty.
    ),
    typ_jsonb -> 'options'
  ) || CASE
    WHEN (typ_jsonb -> 'options' ->> 'array')::boolean THEN
      '[]'
    ELSE ''
  END
)
$$;


--
-- Name: build_type_text_complete(jsonb, text); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.build_type_text_complete(typ_jsonb jsonb, old_type text) RETURNS text
    LANGUAGE sql STRICT
    AS $$/*
Build the text name of a type, using the old type as a base if only options are given.

The main use for this is to allow for altering only the options of the type of a column.

Args:
  typ_jsonb: This is a jsonb denoting the new type.
  old_type: This is the old type name, with no options.

The typ_jsonb should be in the form:
{
  "name": <str> (optional),
  "options": <obj> (optional)
}

*/
SELECT msar.build_type_text(
  jsonb_strip_nulls(
    jsonb_build_object(
      'name', COALESCE(typ_jsonb ->> 'name', old_type),
      'options', typ_jsonb -> 'options'
    )
  )
);
$$;


--
-- Name: build_unique_column_name(oid, smallint); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.build_unique_column_name(tab_id oid, col_id smallint) RETURNS text
    LANGUAGE sql STRICT
    AS $$/*
This function generates a name to be used for a duplicated column.

Given an original column name 'abc', the resulting copies will be named 'abc <n>', where <n> is
minimal (at least 1) subject to the restriction that 'abc <n>' is not already a column of the table
given.

Args:
  tab_id: the table for which we'll generate a column name.
  col_id: the original column whose name we'll use as the prefix in our copied column name.
*/
  SELECT msar.build_unique_column_name(tab_id, attname)
  FROM pg_catalog.pg_attribute pga
  WHERE attrelid=tab_id AND attnum=col_id;
$$;


--
-- Name: build_unique_column_name(regclass, text); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.build_unique_column_name(tab_id regclass, base text) RETURNS text
    LANGUAGE sql STRICT
    AS $$ /*
This function creates a version of the given `base` column name which is unique in a table.

Given an original column name 'abc', the resulting copies will be named 'abc <n>', where <n> is
minimal (at least 1) subject to the restriction that 'abc <n>' is not already a column of the table
given.

Args:
  tab_id: the table for which we'll generate a column name.
  base: the original column name we'll use to build a unique name.
*/
  SELECT CASE
    WHEN NOT EXISTS(
      SELECT 1 FROM pg_catalog.pg_attribute pga WHERE attrelid=tab_id AND attname=base
    ) THEN base
    ELSE msar.build_unique_column_name(tab_id, base, 1)
  END;
$$;


--
-- Name: build_unique_column_name(regclass, text, integer); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.build_unique_column_name(tab_id regclass, base text, idx integer) RETURNS text
    LANGUAGE sql STRICT
    AS $$/*
This function creates a version of the given `base` column name which is unique in a table.

Given an original column name 'abc', the resulting copies will be named 'abc <n>', where <n> is
minimal (at least 1) subject to the restriction that 'abc <n>' is not already a column of the table
given. The given idx is attempted as a suffix to make the column name unique. If the result isn't
unique after all, it's incremented by further calls.

Args:
  tab_id: the table for which we'll generate a column name.
  base: the original column name we'll use to build a unique name.
  idx: an integer to use as a suffix for making the name unique.
*/
  SELECT CASE
    WHEN NOT EXISTS(
      SELECT 1 FROM pg_catalog.pg_attribute pga
      WHERE attrelid=tab_id AND attname=concat(base, ' ', idx)
    ) THEN concat(base, ' ', idx)
    ELSE msar.build_unique_column_name(tab_id, base, idx + 1)
  END;
$$;


--
-- Name: build_unique_fkey_column_name(oid, text, text); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.build_unique_fkey_column_name(tab_id oid, fk_col_name text, frel_name text) RETURNS text
    LANGUAGE plpgsql
    AS $$/*
Create a unique name for a foreign key column.

Args:
  tab_id: The OID of the table where the column name should be unique.
  fk_col_name: The base name for the foreign key column.
  frel_name: The name of the referent table. Used for creating fk_col_name if not given.

Note that frel_name will be used to build the foreign key column name if it's not given. The result
will be of the form: <frel_name>_id. Then, we apply some logic to ensure the result is unique.
*/
BEGIN
  fk_col_name := COALESCE(fk_col_name, format('%s_id', frel_name));
  RETURN msar.build_unique_column_name(tab_id, fk_col_name);
END;
$$;


--
-- Name: build_unqualified_columns_expr(regclass, smallint[]); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.build_unqualified_columns_expr(tab_id regclass, col_ids smallint[]) RETURNS text
    LANGUAGE sql STABLE STRICT
    AS $$/*
*/
SELECT string_agg(format('%I', attname), ', ')
FROM pg_catalog.pg_attribute JOIN unnest(col_ids) x(a) ON attnum = x.a
WHERE
  attrelid = tab_id;
$$;


--
-- Name: build_update_expr(oid, jsonb); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.build_update_expr(tab_id oid, rec_def jsonb) RETURNS text
    LANGUAGE sql STABLE STRICT
    AS $$
SELECT
  format(
    'UPDATE %I.%I SET (%s) = ROW(%s)',
    msar.get_relation_schema_name(tab_id),
    msar.get_relation_name(tab_id),
    string_agg(format('%I', msar.get_column_name(tab_id, key::smallint)), ', '),
    string_agg(format('%L', value), ', ')
  )
FROM jsonb_each_text(rec_def);
$$;


--
-- Name: build_where_clause(oid, jsonb); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.build_where_clause(rel_id oid, tree jsonb) RETURNS text
    LANGUAGE sql STABLE STRICT
    AS $$
SELECT 'WHERE ' || msar.build_expr(rel_id, tree);
$$;


--
-- Name: cast_to__double_quote_char_double_quote_(mathesar_types.multicurrency_money); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to__double_quote_char_double_quote_(mathesar_types.multicurrency_money) RETURNS "char"
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to__double_quote_char_double_quote_(bit); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to__double_quote_char_double_quote_(bit) RETURNS "char"
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to__double_quote_char_double_quote_(boolean); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to__double_quote_char_double_quote_(boolean) RETURNS "char"
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to__double_quote_char_double_quote_(bytea); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to__double_quote_char_double_quote_(bytea) RETURNS "char"
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to__double_quote_char_double_quote_(cidr); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to__double_quote_char_double_quote_(cidr) RETURNS "char"
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to__double_quote_char_double_quote_(date); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to__double_quote_char_double_quote_(date) RETURNS "char"
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to__double_quote_char_double_quote_(daterange); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to__double_quote_char_double_quote_(daterange) RETURNS "char"
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to__double_quote_char_double_quote_(real); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to__double_quote_char_double_quote_(real) RETURNS "char"
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to__double_quote_char_double_quote_(double precision); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to__double_quote_char_double_quote_(double precision) RETURNS "char"
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to__double_quote_char_double_quote_(inet); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to__double_quote_char_double_quote_(inet) RETURNS "char"
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to__double_quote_char_double_quote_(smallint); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to__double_quote_char_double_quote_(smallint) RETURNS "char"
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to__double_quote_char_double_quote_(integer); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to__double_quote_char_double_quote_(integer) RETURNS "char"
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to__double_quote_char_double_quote_(int4range); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to__double_quote_char_double_quote_(int4range) RETURNS "char"
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to__double_quote_char_double_quote_(bigint); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to__double_quote_char_double_quote_(bigint) RETURNS "char"
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to__double_quote_char_double_quote_(int8range); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to__double_quote_char_double_quote_(int8range) RETURNS "char"
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to__double_quote_char_double_quote_(interval); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to__double_quote_char_double_quote_(interval) RETURNS "char"
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to__double_quote_char_double_quote_(json); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to__double_quote_char_double_quote_(json) RETURNS "char"
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to__double_quote_char_double_quote_(jsonb); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to__double_quote_char_double_quote_(jsonb) RETURNS "char"
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to__double_quote_char_double_quote_(macaddr); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to__double_quote_char_double_quote_(macaddr) RETURNS "char"
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to__double_quote_char_double_quote_(money); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to__double_quote_char_double_quote_(money) RETURNS "char"
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to__double_quote_char_double_quote_(numeric); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to__double_quote_char_double_quote_(numeric) RETURNS "char"
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to__double_quote_char_double_quote_(numrange); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to__double_quote_char_double_quote_(numrange) RETURNS "char"
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to__double_quote_char_double_quote_(oid); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to__double_quote_char_double_quote_(oid) RETURNS "char"
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to__double_quote_char_double_quote_(regclass); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to__double_quote_char_double_quote_(regclass) RETURNS "char"
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to__double_quote_char_double_quote_(text); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to__double_quote_char_double_quote_(text) RETURNS "char"
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to__double_quote_char_double_quote_(time without time zone); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to__double_quote_char_double_quote_(time without time zone) RETURNS "char"
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to__double_quote_char_double_quote_(timestamp without time zone); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to__double_quote_char_double_quote_(timestamp without time zone) RETURNS "char"
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to__double_quote_char_double_quote_(timestamp with time zone); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to__double_quote_char_double_quote_(timestamp with time zone) RETURNS "char"
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to__double_quote_char_double_quote_(time with time zone); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to__double_quote_char_double_quote_(time with time zone) RETURNS "char"
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to__double_quote_char_double_quote_(tsrange); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to__double_quote_char_double_quote_(tsrange) RETURNS "char"
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to__double_quote_char_double_quote_(tstzrange); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to__double_quote_char_double_quote_(tstzrange) RETURNS "char"
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to__double_quote_char_double_quote_(tsvector); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to__double_quote_char_double_quote_(tsvector) RETURNS "char"
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to__double_quote_char_double_quote_(uuid); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to__double_quote_char_double_quote_(uuid) RETURNS "char"
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_bigint(boolean); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_bigint(boolean) RETURNS bigint
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT CASE WHEN $1 THEN 1::bigint ELSE 0::bigint END;
$_$;


--
-- Name: cast_to_bigint(real); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_bigint(real) RETURNS bigint
    LANGUAGE plpgsql STRICT
    AS $_$
  DECLARE integer_res bigint;
  BEGIN
    SELECT $1::bigint INTO integer_res;
    IF integer_res = $1 THEN
      RETURN integer_res;
    END IF;
    RAISE EXCEPTION '% cannot be cast to bigint without loss', $1;
  END;
$_$;


--
-- Name: cast_to_bigint(double precision); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_bigint(double precision) RETURNS bigint
    LANGUAGE plpgsql STRICT
    AS $_$
  DECLARE integer_res bigint;
  BEGIN
    SELECT $1::bigint INTO integer_res;
    IF integer_res = $1 THEN
      RETURN integer_res;
    END IF;
    RAISE EXCEPTION '% cannot be cast to bigint without loss', $1;
  END;
$_$;


--
-- Name: cast_to_bigint(bigint); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_bigint(bigint) RETURNS bigint
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::bigint;
$_$;


--
-- Name: cast_to_bigint(money); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_bigint(money) RETURNS bigint
    LANGUAGE plpgsql STRICT
    AS $_$
  DECLARE integer_res bigint;
  BEGIN
    SELECT $1::bigint INTO integer_res;
    IF integer_res = $1 THEN
      RETURN integer_res;
    END IF;
    RAISE EXCEPTION '% cannot be cast to bigint without loss', $1;
  END;
$_$;


--
-- Name: cast_to_bigint(numeric); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_bigint(numeric) RETURNS bigint
    LANGUAGE plpgsql STRICT
    AS $_$
  DECLARE integer_res bigint;
  BEGIN
    SELECT $1::bigint INTO integer_res;
    IF integer_res = $1 THEN
      RETURN integer_res;
    END IF;
    RAISE EXCEPTION '% cannot be cast to bigint without loss', $1;
  END;
$_$;


--
-- Name: cast_to_bigint(text); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_bigint(text) RETURNS bigint
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::bigint;
$_$;


--
-- Name: cast_to_boolean(boolean); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_boolean(boolean) RETURNS boolean
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::boolean;
$_$;


--
-- Name: cast_to_boolean(real); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_boolean(real) RETURNS boolean
    LANGUAGE plpgsql STRICT
    AS $_$
BEGIN
  IF $1<>0 AND $1<>1 THEN
    RAISE EXCEPTION '% is not a boolean', $1; END IF;
  RETURN $1<>0;
END;
$_$;


--
-- Name: cast_to_boolean(double precision); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_boolean(double precision) RETURNS boolean
    LANGUAGE plpgsql STRICT
    AS $_$
BEGIN
  IF $1<>0 AND $1<>1 THEN
    RAISE EXCEPTION '% is not a boolean', $1; END IF;
  RETURN $1<>0;
END;
$_$;


--
-- Name: cast_to_boolean(bigint); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_boolean(bigint) RETURNS boolean
    LANGUAGE plpgsql STRICT
    AS $_$
BEGIN
  IF $1<>0 AND $1<>1 THEN
    RAISE EXCEPTION '% is not a boolean', $1; END IF;
  RETURN $1<>0;
END;
$_$;


--
-- Name: cast_to_boolean(numeric); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_boolean(numeric) RETURNS boolean
    LANGUAGE plpgsql STRICT
    AS $_$
BEGIN
  IF $1<>0 AND $1<>1 THEN
    RAISE EXCEPTION '% is not a boolean', $1; END IF;
  RETURN $1<>0;
END;
$_$;


--
-- Name: cast_to_boolean(text); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_boolean(text) RETURNS boolean
    LANGUAGE plpgsql STRICT
    AS $_$
DECLARE
  istrue boolean;
BEGIN
  SELECT
    $1='1' OR lower($1) = 'on'
    OR lower($1)='t' OR lower($1)='true'
    OR lower($1)='y' OR lower($1)='yes'
  INTO istrue;
  IF istrue
    OR $1='0' OR lower($1) = 'off'
    OR lower($1)='f' OR lower($1)='false'
    OR lower($1)='n' OR lower($1)='no'
  THEN
    RETURN istrue;
  END IF;
  RAISE EXCEPTION '% is not a boolean', $1;
END;
$_$;


--
-- Name: cast_to_character(mathesar_types.multicurrency_money); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_character(mathesar_types.multicurrency_money) RETURNS character
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_character(bit); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_character(bit) RETURNS character
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_character(boolean); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_character(boolean) RETURNS character
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_character(bytea); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_character(bytea) RETURNS character
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_character(cidr); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_character(cidr) RETURNS character
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_character(date); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_character(date) RETURNS character
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_character(daterange); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_character(daterange) RETURNS character
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_character(real); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_character(real) RETURNS character
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_character(double precision); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_character(double precision) RETURNS character
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_character(inet); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_character(inet) RETURNS character
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_character(smallint); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_character(smallint) RETURNS character
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_character(integer); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_character(integer) RETURNS character
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_character(int4range); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_character(int4range) RETURNS character
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_character(bigint); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_character(bigint) RETURNS character
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_character(int8range); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_character(int8range) RETURNS character
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_character(interval); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_character(interval) RETURNS character
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_character(json); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_character(json) RETURNS character
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_character(jsonb); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_character(jsonb) RETURNS character
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_character(macaddr); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_character(macaddr) RETURNS character
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_character(money); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_character(money) RETURNS character
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_character(numeric); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_character(numeric) RETURNS character
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_character(numrange); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_character(numrange) RETURNS character
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_character(oid); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_character(oid) RETURNS character
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_character(regclass); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_character(regclass) RETURNS character
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_character(text); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_character(text) RETURNS character
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_character(time without time zone); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_character(time without time zone) RETURNS character
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_character(timestamp without time zone); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_character(timestamp without time zone) RETURNS character
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_character(timestamp with time zone); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_character(timestamp with time zone) RETURNS character
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_character(time with time zone); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_character(time with time zone) RETURNS character
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_character(tsrange); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_character(tsrange) RETURNS character
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_character(tstzrange); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_character(tstzrange) RETURNS character
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_character(tsvector); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_character(tsvector) RETURNS character
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_character(uuid); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_character(uuid) RETURNS character
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_character_varying(mathesar_types.multicurrency_money); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_character_varying(mathesar_types.multicurrency_money) RETURNS character varying
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_character_varying(bit); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_character_varying(bit) RETURNS character varying
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_character_varying(boolean); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_character_varying(boolean) RETURNS character varying
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_character_varying(bytea); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_character_varying(bytea) RETURNS character varying
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_character_varying(cidr); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_character_varying(cidr) RETURNS character varying
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_character_varying(date); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_character_varying(date) RETURNS character varying
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_character_varying(daterange); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_character_varying(daterange) RETURNS character varying
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_character_varying(real); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_character_varying(real) RETURNS character varying
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_character_varying(double precision); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_character_varying(double precision) RETURNS character varying
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_character_varying(inet); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_character_varying(inet) RETURNS character varying
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_character_varying(smallint); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_character_varying(smallint) RETURNS character varying
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_character_varying(integer); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_character_varying(integer) RETURNS character varying
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_character_varying(int4range); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_character_varying(int4range) RETURNS character varying
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_character_varying(bigint); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_character_varying(bigint) RETURNS character varying
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_character_varying(int8range); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_character_varying(int8range) RETURNS character varying
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_character_varying(interval); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_character_varying(interval) RETURNS character varying
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_character_varying(json); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_character_varying(json) RETURNS character varying
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_character_varying(jsonb); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_character_varying(jsonb) RETURNS character varying
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_character_varying(macaddr); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_character_varying(macaddr) RETURNS character varying
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_character_varying(money); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_character_varying(money) RETURNS character varying
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_character_varying(numeric); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_character_varying(numeric) RETURNS character varying
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_character_varying(numrange); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_character_varying(numrange) RETURNS character varying
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_character_varying(oid); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_character_varying(oid) RETURNS character varying
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_character_varying(regclass); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_character_varying(regclass) RETURNS character varying
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_character_varying(text); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_character_varying(text) RETURNS character varying
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_character_varying(time without time zone); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_character_varying(time without time zone) RETURNS character varying
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_character_varying(timestamp without time zone); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_character_varying(timestamp without time zone) RETURNS character varying
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_character_varying(timestamp with time zone); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_character_varying(timestamp with time zone) RETURNS character varying
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_character_varying(time with time zone); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_character_varying(time with time zone) RETURNS character varying
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_character_varying(tsrange); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_character_varying(tsrange) RETURNS character varying
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_character_varying(tstzrange); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_character_varying(tstzrange) RETURNS character varying
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_character_varying(tsvector); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_character_varying(tsvector) RETURNS character varying
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_character_varying(uuid); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_character_varying(uuid) RETURNS character varying
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_date(date); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_date(date) RETURNS date
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::timestamp with time zone;
$_$;


--
-- Name: cast_to_date(text); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_date(text) RETURNS date
    LANGUAGE plpgsql STRICT
    AS $_$
DECLARE
  timestamp_value_with_tz NUMERIC;
  timestamp_value NUMERIC;
  date_value NUMERIC;
BEGIN
  SET LOCAL TIME ZONE 'UTC';
  SELECT EXTRACT(EPOCH FROM $1::TIMESTAMP WITH TIME ZONE ) INTO timestamp_value_with_tz;
  SELECT EXTRACT(EPOCH FROM $1::TIMESTAMP WITHOUT TIME ZONE) INTO timestamp_value;
  SELECT EXTRACT(EPOCH FROM $1::DATE ) INTO date_value;
  IF (timestamp_value_with_tz = date_value) THEN
    RETURN $1::date;
  END IF;
  RAISE EXCEPTION '% is not a date', $1;
END;
$_$;


--
-- Name: cast_to_date(timestamp with time zone); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_date(timestamp with time zone) RETURNS date
    LANGUAGE plpgsql STRICT
    AS $_$
DECLARE
  timestamp_value_with_tz NUMERIC;
  timestamp_value NUMERIC;
  date_value NUMERIC;
BEGIN
  SET LOCAL TIME ZONE 'UTC';
  SELECT EXTRACT(EPOCH FROM $1::TIMESTAMP WITH TIME ZONE ) INTO timestamp_value_with_tz;
  SELECT EXTRACT(EPOCH FROM $1::TIMESTAMP WITHOUT TIME ZONE) INTO timestamp_value;
  SELECT EXTRACT(EPOCH FROM $1::DATE ) INTO date_value;
  IF (timestamp_value_with_tz = date_value) THEN
    RETURN $1::date;
  END IF;
  RAISE EXCEPTION '% is not a date', $1;
END;
$_$;


--
-- Name: cast_to_double_precision(boolean); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_double_precision(boolean) RETURNS double precision
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT CASE WHEN $1 THEN 1::double precision ELSE 0::double precision END;
$_$;


--
-- Name: cast_to_double_precision(real); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_double_precision(real) RETURNS double precision
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::double precision;
$_$;


--
-- Name: cast_to_double_precision(double precision); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_double_precision(double precision) RETURNS double precision
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::double precision;
$_$;


--
-- Name: cast_to_double_precision(bigint); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_double_precision(bigint) RETURNS double precision
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::double precision;
$_$;


--
-- Name: cast_to_double_precision(numeric); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_double_precision(numeric) RETURNS double precision
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::double precision;
$_$;


--
-- Name: cast_to_double_precision(text); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_double_precision(text) RETURNS double precision
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::double precision;
$_$;


--
-- Name: cast_to_email(text); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_email(text) RETURNS mathesar_types.email
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::mathesar_types.email;
$_$;


--
-- Name: cast_to_integer(boolean); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_integer(boolean) RETURNS integer
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT CASE WHEN $1 THEN 1::integer ELSE 0::integer END;
$_$;


--
-- Name: cast_to_integer(real); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_integer(real) RETURNS integer
    LANGUAGE plpgsql STRICT
    AS $_$
  DECLARE integer_res integer;
  BEGIN
    SELECT $1::integer INTO integer_res;
    IF integer_res = $1 THEN
      RETURN integer_res;
    END IF;
    RAISE EXCEPTION '% cannot be cast to integer without loss', $1;
  END;
$_$;


--
-- Name: cast_to_integer(double precision); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_integer(double precision) RETURNS integer
    LANGUAGE plpgsql STRICT
    AS $_$
  DECLARE integer_res integer;
  BEGIN
    SELECT $1::integer INTO integer_res;
    IF integer_res = $1 THEN
      RETURN integer_res;
    END IF;
    RAISE EXCEPTION '% cannot be cast to integer without loss', $1;
  END;
$_$;


--
-- Name: cast_to_integer(bigint); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_integer(bigint) RETURNS integer
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::integer;
$_$;


--
-- Name: cast_to_integer(money); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_integer(money) RETURNS integer
    LANGUAGE plpgsql STRICT
    AS $_$
  DECLARE integer_res integer;
  BEGIN
    SELECT $1::integer INTO integer_res;
    IF integer_res = $1 THEN
      RETURN integer_res;
    END IF;
    RAISE EXCEPTION '% cannot be cast to integer without loss', $1;
  END;
$_$;


--
-- Name: cast_to_integer(numeric); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_integer(numeric) RETURNS integer
    LANGUAGE plpgsql STRICT
    AS $_$
  DECLARE integer_res integer;
  BEGIN
    SELECT $1::integer INTO integer_res;
    IF integer_res = $1 THEN
      RETURN integer_res;
    END IF;
    RAISE EXCEPTION '% cannot be cast to integer without loss', $1;
  END;
$_$;


--
-- Name: cast_to_integer(text); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_integer(text) RETURNS integer
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::integer;
$_$;


--
-- Name: cast_to_interval(interval); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_interval(interval) RETURNS interval
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1;
$_$;


--
-- Name: cast_to_interval(text); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_interval(text) RETURNS interval
    LANGUAGE plpgsql STRICT
    AS $_$
BEGIN
  PERFORM $1::numeric;
  RAISE EXCEPTION '% is a numeric', $1;
  EXCEPTION
    WHEN sqlstate '22P02' THEN
      RETURN $1::interval;
END;
$_$;


--
-- Name: cast_to_json(json); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_json(json) RETURNS json
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::json;
$_$;


--
-- Name: cast_to_json(jsonb); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_json(jsonb) RETURNS json
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::json;
$_$;


--
-- Name: cast_to_json(text); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_json(text) RETURNS json
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::json;
$_$;


--
-- Name: cast_to_jsonb(json); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_jsonb(json) RETURNS jsonb
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::jsonb;
$_$;


--
-- Name: cast_to_jsonb(jsonb); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_jsonb(jsonb) RETURNS jsonb
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::jsonb;
$_$;


--
-- Name: cast_to_jsonb(text); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_jsonb(text) RETURNS jsonb
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::jsonb;
$_$;


--
-- Name: cast_to_mathesar_json_array(json); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_mathesar_json_array(json) RETURNS mathesar_types.mathesar_json_array
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::mathesar_types.mathesar_json_array;
$_$;


--
-- Name: cast_to_mathesar_json_array(jsonb); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_mathesar_json_array(jsonb) RETURNS mathesar_types.mathesar_json_array
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::mathesar_types.mathesar_json_array;
$_$;


--
-- Name: cast_to_mathesar_json_array(text); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_mathesar_json_array(text) RETURNS mathesar_types.mathesar_json_array
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::mathesar_types.mathesar_json_array;
$_$;


--
-- Name: cast_to_mathesar_json_object(json); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_mathesar_json_object(json) RETURNS mathesar_types.mathesar_json_object
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::mathesar_types.mathesar_json_object;
$_$;


--
-- Name: cast_to_mathesar_json_object(jsonb); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_mathesar_json_object(jsonb) RETURNS mathesar_types.mathesar_json_object
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::mathesar_types.mathesar_json_object;
$_$;


--
-- Name: cast_to_mathesar_json_object(text); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_mathesar_json_object(text) RETURNS mathesar_types.mathesar_json_object
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::mathesar_types.mathesar_json_object;
$_$;


--
-- Name: cast_to_mathesar_money(real); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_mathesar_money(real) RETURNS mathesar_types.mathesar_money
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::numeric::mathesar_types.mathesar_money;
$_$;


--
-- Name: cast_to_mathesar_money(double precision); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_mathesar_money(double precision) RETURNS mathesar_types.mathesar_money
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::numeric::mathesar_types.mathesar_money;
$_$;


--
-- Name: cast_to_mathesar_money(bigint); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_mathesar_money(bigint) RETURNS mathesar_types.mathesar_money
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::numeric::mathesar_types.mathesar_money;
$_$;


--
-- Name: cast_to_mathesar_money(money); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_mathesar_money(money) RETURNS mathesar_types.mathesar_money
    LANGUAGE plpgsql STRICT
    AS $_$
DECLARE
  decimal_point text;
  is_negative boolean;
  money_arr text[];
  money_num text;
BEGIN
  SELECT msar.get_mathesar_money_array($1::text) INTO money_arr;
  IF money_arr IS NULL THEN
    RAISE EXCEPTION '% cannot be cast to mathesar_types.mathesar_money', $1;
  END IF;
  SELECT money_arr[1] INTO money_num;
  SELECT ltrim(to_char(1, 'D'), ' ') INTO decimal_point;
  IF money_arr[2] IS NOT NULL THEN
    SELECT regexp_replace(money_num, money_arr[2], '', 'gq') INTO money_num;
  END IF;
  IF money_arr[3] IS NOT NULL THEN
    SELECT regexp_replace(money_num, money_arr[3], decimal_point, 'q') INTO money_num;
  END IF;
  RETURN money_num::mathesar_types.mathesar_money;
END;
$_$;


--
-- Name: cast_to_mathesar_money(numeric); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_mathesar_money(numeric) RETURNS mathesar_types.mathesar_money
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::numeric::mathesar_types.mathesar_money;
$_$;


--
-- Name: cast_to_mathesar_money(text); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_mathesar_money(text) RETURNS mathesar_types.mathesar_money
    LANGUAGE plpgsql STRICT
    AS $_$
DECLARE
  decimal_point text;
  is_negative boolean;
  money_arr text[];
  money_num text;
BEGIN
  SELECT msar.get_mathesar_money_array($1::text) INTO money_arr;
  IF money_arr IS NULL THEN
    RAISE EXCEPTION '% cannot be cast to mathesar_types.mathesar_money', $1;
  END IF;
  SELECT money_arr[1] INTO money_num;
  SELECT ltrim(to_char(1, 'D'), ' ') INTO decimal_point;
  IF money_arr[2] IS NOT NULL THEN
    SELECT regexp_replace(money_num, money_arr[2], '', 'gq') INTO money_num;
  END IF;
  IF money_arr[3] IS NOT NULL THEN
    SELECT regexp_replace(money_num, money_arr[3], decimal_point, 'q') INTO money_num;
  END IF;
  RETURN money_num::mathesar_types.mathesar_money;
END;
$_$;


--
-- Name: cast_to_mathesar_money(text, "char", "char", text, text); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_mathesar_money(num text, group_sep "char", decimal_p "char", curr_pref text, curr_suff text) RETURNS mathesar_types.mathesar_money
    LANGUAGE sql STRICT
    AS $_$
  SELECT CASE WHEN num ~ '^.*(-|\(.+\)).*$' THEN -- Handle negative values
    ('-' || replace(replace(replace(replace(replace(replace(replace(num, '-', ''), '(', ''), ')', ''), curr_pref, ''), curr_suff, ''), group_sep, ''), decimal_p, ltrim(to_char(1, 'D'), ' ')))::mathesar_types.mathesar_money
  ELSE
    replace(replace(replace(replace(num, curr_pref, ''), curr_suff, ''), group_sep, ''), decimal_p, ltrim(to_char(1, 'D'), ' '))::mathesar_types.mathesar_money
  END;
$_$;


--
-- Name: cast_to_money(real); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_money(real) RETURNS money
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::numeric::money;
$_$;


--
-- Name: cast_to_money(double precision); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_money(double precision) RETURNS money
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::numeric::money;
$_$;


--
-- Name: cast_to_money(bigint); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_money(bigint) RETURNS money
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::numeric::money;
$_$;


--
-- Name: cast_to_money(money); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_money(money) RETURNS money
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::money;
$_$;


--
-- Name: cast_to_money(numeric); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_money(numeric) RETURNS money
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::numeric::money;
$_$;


--
-- Name: cast_to_money(text); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_money(text) RETURNS money
    LANGUAGE plpgsql STRICT
    AS $_$
DECLARE
  currency text;
BEGIN
  SELECT to_char(1, 'L') INTO currency;
  IF ($1 LIKE '%' || currency) OR ($1 LIKE currency || '%') THEN
    RETURN $1::money;
  END IF;
  RAISE EXCEPTION '% cannot be cast to money as currency symbol is missing', $1;
END;
$_$;


--
-- Name: cast_to_multicurrency_money(mathesar_types.multicurrency_money); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_multicurrency_money(mathesar_types.multicurrency_money) RETURNS mathesar_types.multicurrency_money
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::mathesar_types.multicurrency_money;
$_$;


--
-- Name: cast_to_multicurrency_money(real); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_multicurrency_money(real) RETURNS mathesar_types.multicurrency_money
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT ROW($1, 'USD')::mathesar_types.multicurrency_money;
$_$;


--
-- Name: cast_to_multicurrency_money(double precision); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_multicurrency_money(double precision) RETURNS mathesar_types.multicurrency_money
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT ROW($1, 'USD')::mathesar_types.multicurrency_money;
$_$;


--
-- Name: cast_to_multicurrency_money(bigint); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_multicurrency_money(bigint) RETURNS mathesar_types.multicurrency_money
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT ROW($1, 'USD')::mathesar_types.multicurrency_money;
$_$;


--
-- Name: cast_to_multicurrency_money(money); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_multicurrency_money(money) RETURNS mathesar_types.multicurrency_money
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT ROW($1, 'USD')::mathesar_types.multicurrency_money;
$_$;


--
-- Name: cast_to_multicurrency_money(numeric); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_multicurrency_money(numeric) RETURNS mathesar_types.multicurrency_money
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT ROW($1, 'USD')::mathesar_types.multicurrency_money;
$_$;


--
-- Name: cast_to_multicurrency_money(text); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_multicurrency_money(text) RETURNS mathesar_types.multicurrency_money
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT ROW($1, 'USD')::mathesar_types.multicurrency_money;
$_$;


--
-- Name: cast_to_name(mathesar_types.multicurrency_money); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_name(mathesar_types.multicurrency_money) RETURNS name
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_name(bit); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_name(bit) RETURNS name
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_name(boolean); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_name(boolean) RETURNS name
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_name(bytea); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_name(bytea) RETURNS name
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_name(cidr); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_name(cidr) RETURNS name
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_name(date); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_name(date) RETURNS name
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_name(daterange); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_name(daterange) RETURNS name
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_name(real); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_name(real) RETURNS name
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_name(double precision); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_name(double precision) RETURNS name
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_name(inet); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_name(inet) RETURNS name
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_name(smallint); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_name(smallint) RETURNS name
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_name(integer); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_name(integer) RETURNS name
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_name(int4range); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_name(int4range) RETURNS name
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_name(bigint); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_name(bigint) RETURNS name
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_name(int8range); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_name(int8range) RETURNS name
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_name(interval); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_name(interval) RETURNS name
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_name(json); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_name(json) RETURNS name
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_name(jsonb); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_name(jsonb) RETURNS name
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_name(macaddr); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_name(macaddr) RETURNS name
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_name(money); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_name(money) RETURNS name
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_name(numeric); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_name(numeric) RETURNS name
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_name(numrange); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_name(numrange) RETURNS name
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_name(oid); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_name(oid) RETURNS name
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_name(regclass); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_name(regclass) RETURNS name
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_name(text); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_name(text) RETURNS name
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_name(time without time zone); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_name(time without time zone) RETURNS name
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_name(timestamp without time zone); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_name(timestamp without time zone) RETURNS name
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_name(timestamp with time zone); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_name(timestamp with time zone) RETURNS name
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_name(time with time zone); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_name(time with time zone) RETURNS name
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_name(tsrange); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_name(tsrange) RETURNS name
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_name(tstzrange); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_name(tstzrange) RETURNS name
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_name(tsvector); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_name(tsvector) RETURNS name
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_name(uuid); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_name(uuid) RETURNS name
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_numeric(boolean); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_numeric(boolean) RETURNS numeric
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT CASE WHEN $1 THEN 1::numeric ELSE 0::numeric END;
$_$;


--
-- Name: cast_to_numeric(real); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_numeric(real) RETURNS numeric
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::numeric;
$_$;


--
-- Name: cast_to_numeric(double precision); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_numeric(double precision) RETURNS numeric
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::numeric;
$_$;


--
-- Name: cast_to_numeric(bigint); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_numeric(bigint) RETURNS numeric
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::numeric;
$_$;


--
-- Name: cast_to_numeric(money); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_numeric(money) RETURNS numeric
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::numeric;
$_$;


--
-- Name: cast_to_numeric(numeric); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_numeric(numeric) RETURNS numeric
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::numeric;
$_$;


--
-- Name: cast_to_numeric(text); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_numeric(text) RETURNS numeric
    LANGUAGE plpgsql STRICT
    AS $_$
DECLARE
  decimal_point text;
  is_negative boolean;
  numeric_arr text[];
  numeric text;
BEGIN
  SELECT msar.get_numeric_array($1::text) INTO numeric_arr;
  IF numeric_arr IS NULL THEN
    RAISE EXCEPTION '% cannot be cast to numeric', $1;
  END IF;
  SELECT numeric_arr[1] INTO numeric;
  SELECT ltrim(to_char(1, 'D'), ' ') INTO decimal_point;
  SELECT $1::text ~ '^-.*$' INTO is_negative;
  IF numeric_arr[2] IS NOT NULL THEN
    SELECT regexp_replace(numeric, numeric_arr[2], '', 'gq') INTO numeric;
  END IF;
  IF numeric_arr[3] IS NOT NULL THEN
    SELECT regexp_replace(numeric, numeric_arr[3], decimal_point, 'q') INTO numeric;
  END IF;
  IF is_negative THEN
    RETURN ('-' || numeric)::numeric;
  END IF;
  RETURN numeric::numeric;
END;
$_$;


--
-- Name: cast_to_numeric(text, "char", "char"); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_numeric(num text, group_sep "char", decimal_p "char") RETURNS numeric
    LANGUAGE sql STRICT
    AS $$/*
Cast to numeric with prechosen group and decimal separators.

For performance, this function does not check for correctness of the given number format. It simply
replaces and removes characters as directed, then attempts to cast to numeric.

Args:
  num: The string we'll cast to numeric.
  group_sep: Any instance of this character will be removed from `num` before casting.
  decimal_p: Any instance of this character will be replaced with the locale decimal before casting.
*/
SELECT replace(replace(num, group_sep, ''), decimal_p, ltrim(to_char(1, 'D'), ' '))::numeric;
$$;


--
-- Name: cast_to_real(boolean); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_real(boolean) RETURNS real
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT CASE WHEN $1 THEN 1::real ELSE 0::real END;
$_$;


--
-- Name: cast_to_real(real); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_real(real) RETURNS real
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::real;
$_$;


--
-- Name: cast_to_real(double precision); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_real(double precision) RETURNS real
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::real;
$_$;


--
-- Name: cast_to_real(bigint); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_real(bigint) RETURNS real
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::real;
$_$;


--
-- Name: cast_to_real(numeric); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_real(numeric) RETURNS real
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::real;
$_$;


--
-- Name: cast_to_real(text); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_real(text) RETURNS real
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::real;
$_$;


--
-- Name: cast_to_smallint(boolean); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_smallint(boolean) RETURNS smallint
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT CASE WHEN $1 THEN 1::smallint ELSE 0::smallint END;
$_$;


--
-- Name: cast_to_smallint(real); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_smallint(real) RETURNS smallint
    LANGUAGE plpgsql STRICT
    AS $_$
  DECLARE integer_res smallint;
  BEGIN
    SELECT $1::smallint INTO integer_res;
    IF integer_res = $1 THEN
      RETURN integer_res;
    END IF;
    RAISE EXCEPTION '% cannot be cast to smallint without loss', $1;
  END;
$_$;


--
-- Name: cast_to_smallint(double precision); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_smallint(double precision) RETURNS smallint
    LANGUAGE plpgsql STRICT
    AS $_$
  DECLARE integer_res smallint;
  BEGIN
    SELECT $1::smallint INTO integer_res;
    IF integer_res = $1 THEN
      RETURN integer_res;
    END IF;
    RAISE EXCEPTION '% cannot be cast to smallint without loss', $1;
  END;
$_$;


--
-- Name: cast_to_smallint(bigint); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_smallint(bigint) RETURNS smallint
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::smallint;
$_$;


--
-- Name: cast_to_smallint(money); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_smallint(money) RETURNS smallint
    LANGUAGE plpgsql STRICT
    AS $_$
  DECLARE integer_res smallint;
  BEGIN
    SELECT $1::smallint INTO integer_res;
    IF integer_res = $1 THEN
      RETURN integer_res;
    END IF;
    RAISE EXCEPTION '% cannot be cast to smallint without loss', $1;
  END;
$_$;


--
-- Name: cast_to_smallint(numeric); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_smallint(numeric) RETURNS smallint
    LANGUAGE plpgsql STRICT
    AS $_$
  DECLARE integer_res smallint;
  BEGIN
    SELECT $1::smallint INTO integer_res;
    IF integer_res = $1 THEN
      RETURN integer_res;
    END IF;
    RAISE EXCEPTION '% cannot be cast to smallint without loss', $1;
  END;
$_$;


--
-- Name: cast_to_smallint(text); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_smallint(text) RETURNS smallint
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::smallint;
$_$;


--
-- Name: cast_to_text(mathesar_types.multicurrency_money); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_text(mathesar_types.multicurrency_money) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_text(bit); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_text(bit) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_text(boolean); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_text(boolean) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_text(bytea); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_text(bytea) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_text(cidr); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_text(cidr) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_text(date); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_text(date) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_text(daterange); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_text(daterange) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_text(real); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_text(real) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_text(double precision); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_text(double precision) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_text(inet); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_text(inet) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_text(smallint); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_text(smallint) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_text(integer); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_text(integer) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_text(int4range); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_text(int4range) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_text(bigint); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_text(bigint) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_text(int8range); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_text(int8range) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_text(interval); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_text(interval) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_text(json); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_text(json) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_text(jsonb); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_text(jsonb) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_text(macaddr); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_text(macaddr) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_text(money); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_text(money) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_text(numeric); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_text(numeric) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_text(numrange); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_text(numrange) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_text(oid); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_text(oid) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_text(regclass); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_text(regclass) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_text(text); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_text(text) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_text(time without time zone); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_text(time without time zone) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_text(timestamp without time zone); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_text(timestamp without time zone) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_text(timestamp with time zone); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_text(timestamp with time zone) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_text(time with time zone); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_text(time with time zone) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_text(tsrange); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_text(tsrange) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_text(tstzrange); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_text(tstzrange) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_text(tsvector); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_text(tsvector) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_text(uuid); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_text(uuid) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::text;
$_$;


--
-- Name: cast_to_time_with_time_zone(text); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_time_with_time_zone(text) RETURNS time with time zone
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::time with time zone;
$_$;


--
-- Name: cast_to_time_with_time_zone(time with time zone); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_time_with_time_zone(time with time zone) RETURNS time with time zone
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::time with time zone;
$_$;


--
-- Name: cast_to_time_without_time_zone(text); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_time_without_time_zone(text) RETURNS time without time zone
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::time without time zone;
$_$;


--
-- Name: cast_to_time_without_time_zone(time with time zone); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_time_without_time_zone(time with time zone) RETURNS time without time zone
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::time without time zone;
$_$;


--
-- Name: cast_to_timestamp_with_time_zone(text); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_timestamp_with_time_zone(text) RETURNS timestamp with time zone
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::timestamp with time zone;
$_$;


--
-- Name: cast_to_timestamp_with_time_zone(timestamp with time zone); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_timestamp_with_time_zone(timestamp with time zone) RETURNS timestamp with time zone
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::timestamp with time zone;
$_$;


--
-- Name: cast_to_timestamp_without_time_zone(date); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_timestamp_without_time_zone(date) RETURNS timestamp without time zone
    LANGUAGE plpgsql STRICT
    AS $_$
DECLARE
  timestamp_value_with_tz NUMERIC;
  timestamp_value NUMERIC;
  date_value NUMERIC;
BEGIN
  SET LOCAL TIME ZONE 'UTC';
  SELECT EXTRACT(EPOCH FROM $1::TIMESTAMP WITH TIME ZONE ) INTO timestamp_value_with_tz;
  SELECT EXTRACT(EPOCH FROM $1::TIMESTAMP WITHOUT TIME ZONE) INTO timestamp_value;
  SELECT EXTRACT(EPOCH FROM $1::DATE ) INTO date_value;
  IF (timestamp_value_with_tz = timestamp_value) THEN
    RETURN $1::timestamp without time zone;
  END IF;
  RAISE EXCEPTION '% is not a timestamp without time zone', $1;
END;
$_$;


--
-- Name: cast_to_timestamp_without_time_zone(text); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_timestamp_without_time_zone(text) RETURNS timestamp without time zone
    LANGUAGE plpgsql STRICT
    AS $_$
DECLARE
  timestamp_value_with_tz NUMERIC;
  timestamp_value NUMERIC;
  date_value NUMERIC;
BEGIN
  SET LOCAL TIME ZONE 'UTC';
  SELECT EXTRACT(EPOCH FROM $1::TIMESTAMP WITH TIME ZONE ) INTO timestamp_value_with_tz;
  SELECT EXTRACT(EPOCH FROM $1::TIMESTAMP WITHOUT TIME ZONE) INTO timestamp_value;
  SELECT EXTRACT(EPOCH FROM $1::DATE ) INTO date_value;
  IF (timestamp_value_with_tz = timestamp_value) THEN
    RETURN $1::timestamp without time zone;
  END IF;
  RAISE EXCEPTION '% is not a timestamp without time zone', $1;
END;
$_$;


--
-- Name: cast_to_timestamp_without_time_zone(timestamp without time zone); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_timestamp_without_time_zone(timestamp without time zone) RETURNS timestamp without time zone
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::timestamp without time zone;
$_$;


--
-- Name: cast_to_timestamp_without_time_zone(timestamp with time zone); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_timestamp_without_time_zone(timestamp with time zone) RETURNS timestamp without time zone
    LANGUAGE plpgsql STRICT
    AS $_$
DECLARE
  timestamp_value_with_tz NUMERIC;
  timestamp_value NUMERIC;
  date_value NUMERIC;
BEGIN
  SET LOCAL TIME ZONE 'UTC';
  SELECT EXTRACT(EPOCH FROM $1::TIMESTAMP WITH TIME ZONE ) INTO timestamp_value_with_tz;
  SELECT EXTRACT(EPOCH FROM $1::TIMESTAMP WITHOUT TIME ZONE) INTO timestamp_value;
  SELECT EXTRACT(EPOCH FROM $1::DATE ) INTO date_value;
  IF (timestamp_value_with_tz = timestamp_value) THEN
    RETURN $1::timestamp without time zone;
  END IF;
  RAISE EXCEPTION '% is not a timestamp without time zone', $1;
END;
$_$;


--
-- Name: cast_to_uri(text); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_uri(text) RETURNS mathesar_types.uri
    LANGUAGE plpgsql STRICT
    AS $_$
DECLARE
  uri_res mathesar_types.uri := 'https://mathesar.org';
  uri_tld text;
BEGIN
  RETURN $1::mathesar_types.uri;
  EXCEPTION WHEN SQLSTATE '23514' THEN
    SELECT lower(('http://' || $1)::mathesar_types.uri) INTO uri_res;
    SELECT (regexp_match(msar.uri_authority(uri_res), '(?<=\.)(?:.(?!\.))+$'))[1]
      INTO uri_tld;
    IF EXISTS(SELECT 1 FROM msar.top_level_domains WHERE tld = uri_tld) THEN
      RETURN uri_res;
    END IF;
  RAISE EXCEPTION '% is not a mathesar_types.uri', $1;
END;
$_$;


--
-- Name: cast_to_uuid(text); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_uuid(text) RETURNS uuid
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::uuid;
$_$;


--
-- Name: cast_to_uuid(uuid); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.cast_to_uuid(uuid) RETURNS uuid
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
  SELECT $1::uuid;
$_$;


--
-- Name: check_column_mathesar_money_compat(regclass, smallint, numeric); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.check_column_mathesar_money_compat(tab_id regclass, col_id smallint, test_perc numeric, OUT compat_details msar.type_compat_details) RETURNS msar.type_compat_details
    LANGUAGE plpgsql STRICT
    AS $_$/*
Determine whether we can cast the given column to mathesar_money.

Args:
  tab_id: The OID of the table whose column we're checking.
  col_id: The attnum of the column in the table.
  test_perc: The percentage of the table to use for the `cast_to_X` default check.

Returns:
  Information about how to successfully cast the column to mathesar_money.
*/
BEGIN
  compat_details = msar.find_mathesar_money_attrs(
    tab_id, col_id, msar.downsize_table_sample(test_perc)
  );
  EXECUTE format(
    'SELECT msar.cast_to_mathesar_money(%1$I, %5$L, %6$L, %7$L, %8$L) FROM %2$I.%3$I TABLESAMPLE SYSTEM(%4$L);',
    /* %1 */ msar.get_column_name(tab_id, col_id),
    /* %2 */ msar.get_relation_schema_name(tab_id),
    /* %3 */ msar.get_relation_name(tab_id),
    /* %4 */ test_perc,
    /* %5 */ compat_details.group_sep,
    /* %6 */ compat_details.decimal_p,
    /* %7 */ compat_details.curr_pref,
    /* %8 */ compat_details.curr_suff
  );
  compat_details.mathesar_casting = true;
  compat_details.type_compatible = true;
EXCEPTION WHEN OTHERS THEN END;
$_$;


--
-- Name: check_column_numeric_compat(regclass, smallint, numeric); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.check_column_numeric_compat(tab_id regclass, col_id smallint, test_perc numeric, OUT compat_details msar.type_compat_details) RETURNS msar.type_compat_details
    LANGUAGE plpgsql STRICT
    AS $_$/*
Determine whether we can cast the given column to numeric.

Args:
  tab_id: The OID of the table whose column we're checking.
  col_id: The attnum of the column in the table.
  test_perc: The percentage of the table to use for the `cast_to_X` default check.

Returns:
  Information about how to successfully cast the column to numeric.
*/
BEGIN
  compat_details = msar.find_numeric_separators(
    tab_id, col_id, msar.downsize_table_sample(test_perc)
  );
  EXECUTE format(
    'SELECT msar.cast_to_numeric(%1$I, %5$L, %6$L) FROM %2$I.%3$I TABLESAMPLE SYSTEM(%4$L);',
    msar.get_column_name(tab_id, col_id),
    msar.get_relation_schema_name(tab_id),
    msar.get_relation_name(tab_id),
    test_perc,
    compat_details.group_sep,
    compat_details.decimal_p
  );
  compat_details.mathesar_casting = true;
  compat_details.type_compatible = true;
EXCEPTION WHEN OTHERS THEN END;
$_$;


--
-- Name: check_column_type_compat(regclass, smallint, regtype, numeric); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.check_column_type_compat(tab_id regclass, col_id smallint, typ_id regtype, test_perc numeric, OUT compat_details msar.type_compat_details) RETURNS msar.type_compat_details
    LANGUAGE plpgsql STRICT
    AS $_$/*
Get info about the compatibility of a given type for a given column.

Args:
  tab_id: The OID of the table whose column we're checking.
  col_id: The attnum of the column in the table.
  typ_id: The OID of the type we'll check against the column.
  test_perc: The percentage of the table to use for the `cast_to_X` default check.

Returns:
  Info about how to successfully cast the column to the given type.
*/
BEGIN
  CASE typ_id
    WHEN 'numeric'::regtype THEN
      compat_details = msar.check_column_numeric_compat(tab_id, col_id, test_perc);
    WHEN 'mathesar_types.mathesar_money'::regtype THEN
      compat_details = msar.check_column_mathesar_money_compat(tab_id, col_id, test_perc);
    ELSE
      EXECUTE format(
        'SELECT %1$s FROM %2$I.%3$I TABLESAMPLE SYSTEM(%4$L);',
        msar.build_cast_expr(tab_id, col_id, typ_id),
        msar.get_relation_schema_name(tab_id),
        msar.get_relation_name(tab_id),
        test_perc
      );
      compat_details.mathesar_casting = true;
      compat_details.type_compatible = true;
  END CASE;
EXCEPTION WHEN OTHERS THEN END;
$_$;


--
-- Name: col_description(oid, integer); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.col_description(tab_id oid, col_id integer) RETURNS text
    LANGUAGE plpgsql
    AS $$/*
Transparent wrapper for col_description. Putting it in the `msar` namespace helps route all DB calls
from Python through a single Python module.
*/
  BEGIN
    RETURN col_description(tab_id, col_id);
  END
$$;


--
-- Name: column_info_table(regclass); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.column_info_table(tab_id regclass) RETURNS TABLE(id smallint, name name, type text, type_options jsonb, nullable boolean, primary_key boolean, "default" jsonb, has_dependents boolean, description text, current_role_priv jsonb)
    LANGUAGE sql STABLE STRICT
    AS $$
SELECT
  attnum AS id,
  attname AS name,
  CASE WHEN attndims>0 THEN '_array'
    WHEN pgt.typtype = 'e' THEN '_enum'
    ELSE atttypid::regtype::text END AS type,
  msar.get_type_options(atttypid, atttypmod, attndims) AS type_options,
  NOT attnotnull AS nullable,
  COALESCE(pgi.indisprimary, false) AS primary_key,
  msar.describe_column_default(tab_id, attnum) AS default,
  msar.has_dependents(tab_id, attnum) AS has_dependents,
  msar.col_description(tab_id, attnum) AS description,
  msar.list_column_privileges_for_current_role(tab_id, attnum) AS current_role_priv
FROM pg_catalog.pg_attribute pga
  LEFT JOIN pg_catalog.pg_index pgi ON pga.attrelid=pgi.indrelid
    AND pga.attnum=ANY(pgi.indkey) AND pgi.indisprimary
  LEFT JOIN pg_catalog.pg_type pgt ON pga.atttypid=pgt.oid
WHERE pga.attrelid=tab_id AND pga.attnum > 0 and NOT attisdropped;
$$;


--
-- Name: comment_on_column(oid, integer, text); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.comment_on_column(tab_id oid, col_id integer, comment_ text) RETURNS text
    LANGUAGE plpgsql
    AS $$/*
Change the description of a column, returning command executed.

Args:
  tab_id: The OID of the table containg the column whose comment we will change.
  col_id: The ATTNUM of the column whose comment we will change.
  comment_: The new comment.
*/
DECLARE
  comment_sql text;
BEGIN
  SELECT format(
    'COMMENT ON COLUMN %I.%I.%I IS %L',
    msar.get_relation_schema_name(tab_id),
    msar.get_relation_name(tab_id),
    msar.get_column_name(tab_id, col_id),
    comment_
  ) INTO comment_sql;
  EXECUTE comment_sql;
  RETURN comment_sql;
END;
$$;


--
-- Name: comment_on_table(oid, text); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.comment_on_table(tab_id oid, comment_ text) RETURNS void
    LANGUAGE plpgsql
    AS $$/*
Change the description of a table.

Args:
  tab_id: The OID of the table whose comment we will change.
  comment_: The new comment.
*/
BEGIN
  EXECUTE format(
    'COMMENT ON TABLE %I.%I IS %L',
    msar.get_relation_schema_name(tab_id),
    msar.get_relation_name(tab_id),
    comment_
  );
END;
$$;


--
-- Name: copy_column(oid, smallint, text, boolean, boolean); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.copy_column(tab_id oid, col_id smallint, copy_name text, copy_data boolean, copy_constraints boolean) RETURNS smallint
    LANGUAGE plpgsql
    AS $$/*
Copy a column of a table
*/
DECLARE
  col_defs __msar.col_def[];
  tab_name text;
  col_name text;
  created_col_id smallint;
BEGIN
  created_col_id = msar.add_column(
    tab_id,
    jsonb_build_object(
      'name', coalesce(copy_name, msar.build_unique_column_name(tab_id, col_id)),
      'type', jsonb_build_object('id', atttypid, 'modifier', atttypmod),
      'not_null', false,  -- Required since the column will initially be empty.
      'default', CASE WHEN copy_data THEN pg_catalog.pg_get_expr(adbin, tab_id) END,
      'description', msar.col_description(tab_id, attnum)
    ),
    raw_default => true
  )
    FROM pg_catalog.pg_attribute LEFT JOIN pg_catalog.pg_attrdef
      ON adnum=attnum AND adrelid=attrelid
    WHERE attrelid=tab_id AND attnum=col_id;

  IF copy_data THEN
    EXECUTE format(
      'UPDATE %I.%I SET %I=%I',
      msar.get_relation_schema_name(tab_id),
      msar.get_relation_name(tab_id),
      msar.get_column_name(tab_id, created_col_id),
      msar.get_column_name(tab_id, col_id)
    );
  END IF;
  IF copy_constraints THEN
    PERFORM msar.copy_constraint(oid, col_id, created_col_id)
    FROM pg_catalog.pg_constraint
    WHERE conrelid=tab_id AND ARRAY[col_id] <@ conkey;
    PERFORM msar.set_not_null(
      tab_id, created_col_id, attnotnull
    )
    FROM pg_catalog.pg_attribute WHERE attrelid=tab_id AND attnum=col_id;
  END IF;
  RETURN created_col_id;
END;
$$;


--
-- Name: copy_constraint(oid, smallint, smallint); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.copy_constraint(con_id oid, from_col_id smallint, to_col_id smallint) RETURNS oid[]
    LANGUAGE sql STRICT
    AS $$/*
Copy a single constraint associated with a column.

Given a column with attnum 3 involved in the original constraint, and a column with attnum 4 to be
involved in the constraint copy, and other columns 1 and 2 involved in the constraint, suppose the
original constraint had conkey [1, 2, 3]. The copy constraint should then have conkey [1, 2, 4].

For now, this is only implemented for unique constraints.

Args:
  con_id: The oid of the constraint we'll copy.
  from_col_id: The column ID to be removed from the original's conkey in the copy.
  to_col_id: The column ID to be added to the original's conkey in the copy.
*/
WITH
  con_cte AS (SELECT * FROM pg_catalog.pg_constraint WHERE oid=con_id AND contype='u'),
  con_def_cte AS (
    SELECT jsonb_agg(
      jsonb_build_object(
        'name', null,
        'type', con_cte.contype,
        'columns', array_replace(con_cte.conkey, from_col_id, to_col_id)
      )
    ) AS con_def FROM con_cte
  )
SELECT msar.add_constraints(con_cte.conrelid, con_def_cte.con_def) FROM con_cte, con_def_cte;
$$;


--
-- Name: create_role(text, text, boolean); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.create_role(rolename text, password_ text, login_ boolean) RETURNS jsonb
    LANGUAGE plpgsql
    AS $$/*
Creates a login/non-login role, depending on whether the login_ flag is set.
Only the rolename field is required, the password field is required only if login_ is set to true.

Returns a JSON object describing the created role in the form:
  {
    "oid": <int>
    "name": <str>
    "super": <bool>
    "inherits": <bool>
    "create_role": <bool>
    "create_db": <bool>
    "login": <bool>
    "description": <str|null>
    "members": <[
        { "oid": <int>, "admin": <bool> }
      ]|null>
  }

Args:
  rolename: The name of the role to be created, unquoted.
  password_: The password for the rolename to set, unquoted.
  login_: Specify whether the role to be created could login.
*/
BEGIN
  CASE WHEN login_ THEN
    EXECUTE format('CREATE USER %I WITH PASSWORD %L', rolename, password_);
  ELSE
    EXECUTE format('CREATE ROLE %I', rolename);
  END CASE;
  RETURN msar.get_role(rolename);
END;
$$;


--
-- Name: create_schema(text, regrole, text); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.create_schema(sch_name text, own_id regrole, description text DEFAULT ''::text) RETURNS jsonb
    LANGUAGE plpgsql
    AS $$/*
Create a schema, possibly with a description.

If a schema with the given name already exists, an exception will be raised.

Args:
  sch_name: The name of the schema to be created, UNQUOTED.
  own_id:      (optional) The OID of the role who will own the new schema.
  description: (optional) A description for the schema, UNQUOTED.

Returns:
  A json object describing the user-defined schema in the database.

Note:
  - This function does not support IF NOT EXISTS because it's simpler that way. I originally tried
    to support descriptions and if_not_exists in the same function, but as I discovered more edge cases
    and inconsistencies, it got too complex, and I didn't think we'd have a good enough use case for it.
  - If own_id is NULL, the current role will be the owner of the new schema.
*/
DECLARE schema_oid oid;
BEGIN
  EXECUTE 'CREATE SCHEMA ' || quote_ident(sch_name);
  schema_oid := msar.get_schema_oid(sch_name);
  PERFORM msar.set_schema_description(schema_oid, description);
  IF own_id IS NOT NULL THEN
    PERFORM msar.transfer_schema_ownership(schema_oid, own_id);
  END IF;
  RETURN msar.get_schema(schema_oid);
END;
$$;


--
-- Name: degrees_to_month(double precision); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.degrees_to_month(degrees double precision) RETURNS integer
    LANGUAGE sql
    AS $$/*
Convert degrees to discrete month.

To get the fraction of 360°, we divide degrees value by 360 and then to get the equivalent 
fractions of 12 months, we multiply by 12, which is equivalent to divide by 30.

Examples:
     0 =>  1
   120 =>  5
   240 =>  9
   330 => 12
  -120 =>  9
*/
SELECT (((degrees::numeric % 360 + 360) % 360)::double precision / 30)::int + 1;
$$;


--
-- Name: degrees_to_time(double precision); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.degrees_to_time(degrees double precision) RETURNS time without time zone
    LANGUAGE sql
    AS $$/*
Convert given degrees to time (on a 24 hour clock, indexed from midnight).

Steps:
- First, the degrees value is confined to range [0,360°)
- Then the resulting value is converted to time indexed from midnight.

To get the fraction of 360°, we divide degrees value by 360 and then to get the
equivalent fractions of 86400 seconds, we multiply by 86400, which is equivalent
to multiply by 240. 

Examples:
    0 => 00:00:00
   90 => 06:00:00
  180 => 12:00:00
  270 => 18:00:00
  540 => 12:00:00
  -90 => 18:00:00

Inverse of msar.time_to_degrees.
*/
SELECT MAKE_INTERVAL(secs => ((degrees::numeric % 360 + 360) % 360)::double precision * 240)::time;
$$;


--
-- Name: delete_records_from_table(oid, jsonb); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.delete_records_from_table(tab_id oid, rec_ids jsonb) RETURNS jsonb
    LANGUAGE plpgsql STRICT
    AS $_$/*
Delete records from table by id.

Args:
  tab_id: The OID of the table whose record we'll delete.
  rec_ids: An array of primary key values

The table must have a single primary key column.
*/
DECLARE
  pk_id integer;
  ids_deleted jsonb;
BEGIN
  SELECT msar.get_pk_column(tab_id) INTO pk_id;
  EXECUTE format(
    $d$
    WITH delete_cte AS (DELETE FROM %1$I.%2$I %3$s RETURNING *)
    SELECT coalesce(json_agg(%4$I), '[]') FROM delete_cte
    $d$,
    msar.get_relation_schema_name(tab_id),
    msar.get_relation_name(tab_id),
    msar.build_where_clause(
      tab_id, jsonb_build_object(
        'type', 'element_in_json_array_untyped', 'args', jsonb_build_array(
          jsonb_build_object(
            'type', 'format_data', 'args', jsonb_build_array(
              jsonb_build_object('type', 'attnum', 'value', pk_id)
            )
          ),
          jsonb_build_object('type', 'literal', 'value', rec_ids)
        )
      )
    ),
    msar.get_column_name(tab_id, pk_id)
  ) INTO ids_deleted;
  RETURN ids_deleted;
END;
$_$;


--
-- Name: describe_column_default(regclass, smallint); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.describe_column_default(tab_id regclass, col_id smallint) RETURNS jsonb
    LANGUAGE plpgsql STRICT
    AS $$/*
Return a JSONB object describing the default (if any) of the given column in the given table.

The returned JSON will have the form:
  {
    "value": <any>,
    "is_dynamic": <bool>,
  }

If the default is possibly dynamic, i.e., if "is_dynamic" is true, then "value" will be a text SQL
expression that generates the default value if evaluated. If it is not dynamic, then "value" is the
actual default value.
*/
DECLARE
  def_expr text;
  def_json jsonb;
BEGIN
def_expr = CASE
  WHEN attidentity='' THEN pg_catalog.pg_get_expr(adbin, tab_id)
  ELSE 'identity'
END
FROM pg_catalog.pg_attribute LEFT JOIN pg_catalog.pg_attrdef ON attrelid=adrelid AND attnum=adnum
WHERE attrelid=tab_id AND attnum=col_id;
IF def_expr IS NULL THEN
  RETURN NULL;
ELSIF msar.is_default_possibly_dynamic(tab_id, col_id) THEN
  EXECUTE format(
    'SELECT jsonb_build_object(''value'', %L, ''is_dynamic'', true)', def_expr
  ) INTO def_json;
ELSE
  EXECUTE format(
    'SELECT jsonb_build_object(''value'', msar.format_data(%s), ''is_dynamic'', false)', def_expr
  ) INTO def_json;
END IF;
RETURN def_json;
END;
$$;


--
-- Name: downsize_table_sample(numeric); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.downsize_table_sample(test_perc numeric) RETURNS numeric
    LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
    AS $$
  SELECT (test_perc / 100) ^ 2 * 100;
$$;


--
-- Name: drop_col_default(regclass, smallint); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.drop_col_default(tab_id regclass, col_id smallint) RETURNS text
    LANGUAGE plpgsql STRICT
    AS $$/*
Drop a column's default value, returning the text of the expression executed.

Args:
  tab_id: The OID of the table where the column with the default to be dropped lives.
  col_id: The attnum of the column with the undesired default.
*/
DECLARE
  drop_col_default_sql text;
BEGIN
  SELECT format(
    'ALTER TABLE %I.%I ALTER COLUMN %I DROP DEFAULT',
    msar.get_relation_schema_name(tab_id),
    msar.get_relation_name(tab_id),
    msar.get_column_name(tab_id, col_id)
  ) INTO drop_col_default_sql;
  EXECUTE drop_col_default_sql;
  RETURN drop_col_default_sql;
END;
$$;


--
-- Name: drop_columns(oid, integer[]); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.drop_columns(tab_id oid, VARIADIC col_ids integer[]) RETURNS void
    LANGUAGE plpgsql STRICT
    AS $$/*
Drop the given columns from the given table.

Args:
  tab_id: OID of the table whose columns we'll drop.
  col_ids: The attnums of the columns to drop.
*/
BEGIN
  col_ids := array_remove(col_ids, null);
  IF array_length(col_ids, 1) IS NOT NULL THEN
    EXECUTE format(
      'ALTER TABLE %I.%I %s',
      msar.get_relation_schema_name(tab_id),
      msar.get_relation_name(tab_id),
      string_agg(format('DROP COLUMN %I', attname), ', ')
    )
    FROM pg_catalog.pg_attribute AS pga INNER JOIN unnest(col_ids) AS x(col) ON pga.attnum=x.col
    WHERE attrelid=tab_id AND NOT attisdropped;
  END IF;
END;
$$;


--
-- Name: drop_constraint(oid, oid); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.drop_constraint(tab_id oid, con_id oid) RETURNS text
    LANGUAGE plpgsql STRICT
    AS $$/*
Drop a constraint

Args:
  tab_id: OID of the table that has the constraint to be dropped.
  con_id: OID of the constraint to be dropped.
*/
BEGIN
  RETURN msar.drop_constraint(
    msar.get_relation_schema_name(tab_id),
    msar.get_relation_name(tab_id),
    msar.get_constraint_name(con_id)
  );
END;
$$;


--
-- Name: drop_constraint(text, text, text); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.drop_constraint(sch_name text, tab_name text, con_name text) RETURNS text
    LANGUAGE plpgsql STRICT
    AS $$/*
Drop a constraint

Args:
  sch_name: The name of the schema where the table with constraint to be dropped resides, unquoted.
  tab_name: The name of the table that has the constraint to be dropped, unquoted.
  con_name: Name of the constraint to drop, unquoted.
*/
BEGIN
  EXECUTE format('ALTER TABLE %I.%I DROP CONSTRAINT %I', sch_name, tab_name, con_name);
  RETURN con_name;
END;
$$;


--
-- Name: drop_database_query(oid); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.drop_database_query(dat_id oid) RETURNS text
    LANGUAGE plpgsql STRICT
    AS $$/*
Return the SQL query to drop a database.

If no database exists with the given oid, an exception will be raised.

Args:
  dat_id: The OID of the role to drop.
*/
BEGIN
  RETURN format('DROP DATABASE %I', msar.get_database_name(dat_id));
END;
$$;


--
-- Name: drop_role(regrole); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.drop_role(rol_id regrole) RETURNS void
    LANGUAGE plpgsql
    AS $$/*
Drop a role.

Note:
- To drop a superuser role, you must be a superuser yourself.
- To drop non-superuser roles, you must have CREATEROLE privilege and have been granted ADMIN OPTION on the role.

Args:
  rol_id: The OID of the role to drop on the database.
*/
BEGIN
  EXECUTE format('DROP ROLE %I', msar.get_role_name(rol_id));
END;
$$;


--
-- Name: drop_schemas(regnamespace[]); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.drop_schemas(sch_ids regnamespace[]) RETURNS void
    LANGUAGE plpgsql STRICT
    AS $$/*
Safely drop all objects in each schema, then the schemas themselves.

Does not work on the msar schema.

If any passed schema doesn't exist, an exception will be raised. If any object exists in a schema
which isn't passed, but which depends on an object in a passed schema, an exception will be raised.

Args:
  sch_ids: The OIDs of the schemas to drop.
*/
DECLARE
  obj RECORD;
  message text;
  detail text;
  sch regnamespace;
  sch_name text;
  undropped_objects text[];
  drop_success boolean := false;
  drop_failed boolean := false;
BEGIN
  SET client_min_messages = WARNING;
  FOR obj IN
    SELECT obj_id, obj_schema, obj_name, obj_kind
    FROM msar.get_schema_objects_table(sch_ids)
    ORDER BY obj_id DESC  -- Objects more often depend on others of lower OID.
  LOOP
    BEGIN
      EXECUTE format('DROP %s IF EXISTS %I.%I', obj.obj_kind, obj.obj_schema, obj.obj_name);
      drop_success = true;
    EXCEPTION
      WHEN dependent_objects_still_exist THEN
        GET STACKED DIAGNOSTICS
          message = MESSAGE_TEXT,
          detail = PG_EXCEPTION_DETAIL;
        undropped_objects = undropped_objects || message;
        IF detail <> '' THEN
           undropped_objects = undropped_objects || concat('    ', detail);
        END IF;
      drop_failed =  true;
    END;
  END LOOP;
  SET client_min_messages = NOTICE;
  IF drop_failed IS false THEN
    -- We dropped every object that existed in the schemas.
    RAISE NOTICE E'All objects dropped successfully!\n\nDropping schemas...\n\n';
    FOREACH sch IN ARRAY sch_ids
      LOOP
        sch_name = msar.get_schema_name(sch);
        RAISE NOTICE 'Dropping Schema %', sch_name;
        EXECUTE(format('DROP SCHEMA IF EXISTS %I', sch_name));
      END LOOP;
  ELSIF drop_success IS false THEN
    -- We failed to drop anything in the schemas (and failed to drop at least one object).
    RAISE EXCEPTION USING
      MESSAGE = 'Nothing was dropped in this call due to dependent objects.',
      DETAIL = array_to_string(array_remove(undropped_objects, ''), E'\n         '),
      HINT = 'All changes will be reverted.',
      ERRCODE = 'dependent_objects_still_exist';
  ELSE
    -- We did drop some objects, but failed to drop at least one (due to dependencies). Recurse.
    PERFORM msar.drop_schemas(sch_ids);
  END IF;
END;
$$;


--
-- Name: drop_table(oid, boolean); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.drop_table(tab_id oid, cascade_ boolean) RETURNS text
    LANGUAGE plpgsql STRICT
    AS $$/*
Drop a table, returning the fully qualified name of the dropped table.

Args:
  tab_id: The OID of the table to drop
  cascade_: Whether to drop dependent objects.
*/
DECLARE
  relation_name text;
BEGIN
  relation_name := format(
    '%I.%I',
    msar.get_relation_schema_name(tab_id),
    msar.get_relation_name(tab_id)
  );
  EXECUTE format(
    'DROP TABLE %s %s',
    relation_name,
    CASE WHEN cascade_ THEN 'CASCADE' ELSE '' END
  );
  RETURN relation_name;
END;
$$;


--
-- Name: email_domain_name(mathesar_types.email); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.email_domain_name(mathesar_types.email) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
    SELECT split_part($1, '@', 2);
$_$;


--
-- Name: email_local_part(mathesar_types.email); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.email_local_part(mathesar_types.email) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
    SELECT split_part($1, '@', 1);
$_$;


--
-- Name: extract_columns_from_table(oid, integer[], text, text); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.extract_columns_from_table(tab_id oid, col_ids integer[], new_tab_name text, fk_col_name text) RETURNS jsonb
    LANGUAGE plpgsql
    AS $_$/*
Extract columns from a table to create a new table, linked by a foreign key.

Args:
  tab_id: The OID of the table whose columns we'll extract
  col_ids: An array of the attnums of the columns to extract
  new_tab_name: The name of the new table to be made from the extracted columns, unquoted
  fk_col_name: The name to give the new foreign key column in the remainder table (optional)

The extraction takes a set of columns from the table, and creates a new table from the set of
*distinct* tuples those columns comprise. We also add a new foreign key column to the original
 (remainder) table that links it to the new extracted table so they can be easily rejoined. The
 extracted columns are removed from the remainder table.
*/
DECLARE
  extracted_col_defs CONSTANT jsonb := msar.get_extracted_col_def_jsonb(tab_id, col_ids);
  extracted_con_defs CONSTANT jsonb := msar.get_extracted_con_def_jsonb(tab_id, col_ids);
  fkey_name CONSTANT text := msar.build_unique_fkey_column_name(tab_id, fk_col_name, new_tab_name);
  extracted_table_id integer;
  fkey_attnum integer;
BEGIN
  -- Begin by creating a new table with column definitions matching the extracted columns.
  extracted_table_id := msar.add_mathesar_table(
    msar.get_relation_namespace_oid(tab_id),
    new_tab_name,
    NULL,
    extracted_col_defs,
    extracted_con_defs,
    NULL, -- own_id is set to NULL so the current role would be the owner of the extracted table.
    format('Extracted from %s', __msar.get_qualified_relation_name(tab_id))
  ) ->> 'oid';
  -- Create a new fkey column and foreign key linking the original table to the extracted one.
  fkey_attnum := msar.add_foreign_key_column(fkey_name, tab_id, extracted_table_id);
  -- Insert the data from the original table's columns into the extracted columns, and add
  -- appropriate fkey values to the new fkey column in the original table to give the proper
  -- mapping.
  EXECUTE format($t$
    WITH fkey_cte AS (
      SELECT id, %1$s, dense_rank() OVER (ORDER BY %1$s) AS __msar_tmp_id
      FROM %2$s
    ), ins_cte AS (
      INSERT INTO %3$s (%1$s)
      SELECT DISTINCT %1$s FROM fkey_cte ORDER BY %1$s
    )
    UPDATE %2$s SET %4$I=__msar_tmp_id FROM fkey_cte WHERE
    %2$s.id=fkey_cte.id
    $t$,
    -- %1$s  This is a comma separated string of the extracted column names
    string_agg(quote_ident(col_def ->> 'name'), ', '),
    -- %2$s  This is the name of the original (remainder) table
    __msar.get_qualified_relation_name(tab_id),
    -- %3$s  This is the new extracted table name
    __msar.get_qualified_relation_name(extracted_table_id),
    -- %4$I  This is the name of the fkey column in the remainder table.
    fkey_name
  ) FROM jsonb_array_elements(extracted_col_defs) AS col_def;
  -- Drop the original versions of the extracted columns from the original table.
  PERFORM msar.drop_columns(tab_id, variadic col_ids);
  -- In case the user wanted to give a name to the fkey column matching one of the extracted
  -- columns, perform that operation now (since the original will now be dropped from the original
  -- table)
  IF fk_col_name IS NOT NULL AND fk_col_name IN (
    SELECT col_def ->> 'name'
    FROM jsonb_array_elements(extracted_col_defs) AS col_def
  ) THEN
    PERFORM msar.rename_column(tab_id, fkey_attnum, fk_col_name);
  END IF;
  RETURN jsonb_build_array(extracted_table_id, fkey_attnum);
END;
$_$;


--
-- Name: extract_smallints(jsonb); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.extract_smallints(v jsonb) RETURNS smallint[]
    LANGUAGE plpgsql IMMUTABLE STRICT PARALLEL SAFE
    AS $$/*
From the supplied JSONB value, extract all top-level JSONB array elements which can be successfully
cast to PostgreSQL smallint values. Return the resulting array of smallint values.

If the supplied jsonb value is not an array, this function will return an empty array.

If any jsonb array element cannot be cast to a smallint, it will be silently ignored.

This function does not raise any exceptions. It will always return an array.

This function should not be used on large arrays. It will be slow due to the performance
limitations[1] of EXCEPTION blocks.

[1]: https://www.postgresql.org/docs/current/plpgsql-control-structures.html#PLPGSQL-ERROR-TRAPPING

Args:
  v: any JSONB value.
*/
DECLARE
  result smallint[];
  element jsonb;
BEGIN
  FOR element IN SELECT jsonb_array_elements(v)
  LOOP
    BEGIN
      result := result || (element::smallint);
    EXCEPTION
      -- Ignore any elements that can't be cast to smallint.
      WHEN others THEN
        CONTINUE;
    END;
  END LOOP;
  RETURN result;
EXCEPTION
  WHEN others THEN
    RETURN '{}'::smallint[]; -- Return an empty array if the input is not an array.
END;
$$;


--
-- Name: find_mathesar_money_attrs(regclass, smallint, numeric); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.find_mathesar_money_attrs(tab_id regclass, col_id smallint, test_perc numeric, OUT compat_details msar.type_compat_details) RETURNS msar.type_compat_details
    LANGUAGE plpgsql STABLE STRICT PARALLEL SAFE
    AS $_$/*
Given a table column, find group separators, decimal separators, currency prefixes & currency suffixes
for money values in the column.

Throws an error if more than one group separator, decimal separator, currency prefix or currency suffix is found.
*/
DECLARE
  group_sep text[];
  decimal_p text[];
  curr_pref text[];
  curr_suff text[];
BEGIN
EXECUTE format(
  $q$
  WITH moneyarr_cte AS (
    SELECT msar.get_mathesar_money_array(%1$I) AS n FROM %2$I.%3$I TABLESAMPLE SYSTEM(%4$L)
  )
  SELECT
    array_remove(array_agg(DISTINCT n[2]), null),
    array_remove(array_agg(DISTINCT n[3]), null),
    array_remove(array_agg(DISTINCT n[4]), null),
    array_remove(array_agg(DISTINCT n[5]), null)
  FROM moneyarr_cte;
  $q$,
  /* %1 */ msar.get_column_name(tab_id, col_id),
  /* %2 */ msar.get_relation_schema_name(tab_id),
  /* %3 */ msar.get_relation_name(tab_id),
  /* %4 */ test_perc
) INTO group_sep, decimal_p, curr_pref, curr_suff;
IF array_length(group_sep, 1) > 1 THEN RAISE EXCEPTION 'Too many grouping separators found!';
ELSIF array_length(decimal_p, 1) > 1 THEN RAISE EXCEPTION 'Too many decimal separators found!';
ELSIF array_length(curr_pref, 1) > 1 THEN RAISE EXCEPTION 'Too many currency prefixes found!';
ELSIF array_length(curr_suff, 1) > 1 THEN RAISE EXCEPTION 'Too many currency suffixes found!';
ELSE
  compat_details.group_sep := coalesce(group_sep[1], '');
  compat_details.decimal_p := coalesce(decimal_p[1], '');
  compat_details.curr_pref := coalesce(curr_pref[1], '');
  compat_details.curr_suff := coalesce(curr_suff[1], '');
END IF;
END;
$_$;


--
-- Name: find_numeric_separators(regclass, smallint, numeric); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.find_numeric_separators(tab_id regclass, col_id smallint, test_perc numeric, OUT compat_details msar.type_compat_details) RETURNS msar.type_compat_details
    LANGUAGE plpgsql STABLE STRICT PARALLEL SAFE
    AS $_$/*
Given a table column, find group and decimal separators for number values in the column.

Throws an error if more than one group separator or decimal separator is found.
*/
DECLARE
  group_sep text[];
  decimal_p text[];
BEGIN
EXECUTE format(
  $q$
  WITH numarr_cte AS (
    SELECT msar.get_numeric_array(%1$I) AS n FROM %2$I.%3$I TABLESAMPLE SYSTEM(%4$L)
  )
  SELECT array_remove(array_agg(DISTINCT n[2]), null), array_remove(array_agg(DISTINCT n[3]),  null)
  FROM numarr_cte;
  $q$,
  msar.get_column_name(tab_id, col_id),
  msar.get_relation_schema_name(tab_id),
  msar.get_relation_name(tab_id),
  test_perc
) INTO group_sep, decimal_p;
IF array_length(group_sep, 1) > 1 THEN RAISE EXCEPTION 'Too many grouping separators found!';
ELSIF array_length(decimal_p, 1) > 1 THEN RAISE EXCEPTION 'Too many decimal separators found!';
ELSE
  compat_details.group_sep := coalesce(group_sep[1], '');
  compat_details.decimal_p := coalesce(decimal_p[1], '');
END IF;
END;
$_$;


--
-- Name: form_insert(jsonb, jsonb); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.form_insert(field_info_list jsonb, values_ jsonb) RETURNS void
    LANGUAGE plpgsql STRICT
    AS $$/*
Given field_info_list and values_, generates a lookup table for insert, builds and executes an insert statement.

field_info_list should have the folowing form:
[
  {"key": "k1", "parent_key":null, "column_attnum":5, "table_oid":1234, "depth":0},
  {"key": "k3", "parent_key":"k1", "column_attnum":3, "table_oid":4321, "depth":1},
  {"key": "k2", "parent_key":"k1", "column_attnum":2, "table_oid":4321, "depth":1},
]

values_ should be in the form:
{
  "k1": {"type": "create"},
  "k2": "Jane",
  "k3": "Doe"
}

Example of the SQL generated while Inserting into Items table while creating
a new book entry, adding a new Title, creating a new entry for Author, picking a Publisher.

WITH k1_cte AS (
  INSERT INTO "Library Management"."Authors"("First Name", "Last Name") SELECT 'Jerome K.', 'Jerome' RETURNING *
), k0_cte AS (
  INSERT INTO "Library Management"."Books"("Title", "Author", "Publisher") SELECT 'Three men in a Boat', k1_cte.id, '12' FROM k1_cte RETURNING *
) INSERT INTO "Library Management"."Items"("Acquisition Date", "Acquisition Price", "Book") SELECT '2025-10-09', '69.69', k0_cte.id FROM k0_cte RETURNING *
*/
DECLARE
  ins RECORD;
  insert_str text := '';
  insert_stub text;
  insert_count integer;
BEGIN
  SELECT COUNT(*) INTO insert_count FROM msar.build_insert_lookup_table(field_info_list, values_);

  FOR ins IN SELECT * FROM msar.build_insert_lookup_table(field_info_list, values_) LOOP
    insert_stub := 'INSERT INTO ' ||
      ins.table_name || '(' || ins.column_names || ') SELECT ' || ins.values_ ||
      CASE
        WHEN ins.from_cte_name IS NOT NULL THEN CONCAT(' FROM ', ins.from_cte_name)
        ELSE '' END || ' RETURNING *';
    CASE
      WHEN insert_count > 1 AND ins.cte_name IS NOT NULL THEN
        insert_str := CONCAT_WS(',', NULLIF(insert_str, ''), ins.cte_name || ' AS (' || insert_stub || ')');
      WHEN ins.cte_name IS NULL THEN
        insert_str :=  insert_str || insert_stub;
    END CASE;
  END LOOP;

  CASE
    WHEN insert_count > 1 THEN
      insert_str := 'WITH ' || insert_str;
    ELSE NULL;
  END CASE;
  EXECUTE insert_str;
END;
$$;


--
-- Name: format_data(json[]); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.format_data(val json[]) RETURNS text[]
    LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
    AS $$
SELECT val::text[];
$$;


--
-- Name: format_data(jsonb[]); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.format_data(val jsonb[]) RETURNS text[]
    LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
    AS $$
SELECT val::text[];
$$;


--
-- Name: format_data(anyelement); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.format_data(val anyelement) RETURNS anyelement
    LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
    AS $$
SELECT val;
$$;


--
-- Name: format_data(date); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.format_data(val date) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
    AS $$
SELECT to_char(val, 'YYYY-MM-DD AD');
$$;


--
-- Name: format_data(interval); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.format_data(val interval) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
    AS $$
SELECT concat(
  to_char(val, 'PFMYYYY"Y"FMMM"M"FMDD"D""T"FMHH24"H"FMMI"M"'), date_part('seconds', val), 'S'
);
$$;


--
-- Name: format_data(json); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.format_data(val json) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
    AS $$
SELECT val::text;
$$;


--
-- Name: format_data(jsonb); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.format_data(val jsonb) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
    AS $$
SELECT val::text;
$$;


--
-- Name: format_data(time without time zone); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.format_data(val time without time zone) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
    AS $$
SELECT concat(to_char(val, 'HH24:MI'), ':', to_char(date_part('seconds', val), 'FM00.0999999999'));
$$;


--
-- Name: format_data(timestamp without time zone); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.format_data(val timestamp without time zone) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
    AS $$
SELECT
  concat(
    to_char(val, 'YYYY-MM-DD"T"HH24:MI'),
    ':', to_char(date_part('seconds', val), 'FM00.0999999999'),
    to_char(val, ' BC')
  );
$$;


--
-- Name: format_data(timestamp with time zone); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.format_data(val timestamp with time zone) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
    AS $$
SELECT CASE
  WHEN date_part('timezone_hour', val) = 0 AND date_part('timezone_minute', val) = 0
    THEN concat(
      to_char(val, 'YYYY-MM-DD"T"HH24:MI'),
      ':', to_char(date_part('seconds', val), 'FM00.0999999999'), 'Z', to_char(val, ' BC')
    )
  ELSE
    concat(
      to_char(val, 'YYYY-MM-DD"T"HH24:MI'),
      ':', to_char(date_part('seconds', val), 'FM00.0999999999'),
      to_char(date_part('timezone_hour', val), 'S00'),
      ':', ltrim(to_char(date_part('timezone_minute', val), '00'), '+- '), to_char(val, ' BC')
    )
END;
$$;


--
-- Name: format_data(time with time zone); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.format_data(val time with time zone) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
    AS $$
SELECT CASE
  WHEN date_part('timezone_hour', val) = 0 AND date_part('timezone_minute', val) = 0
    THEN concat(
      to_char(date_part('hour', val), 'FM00'), ':', to_char(date_part('minute', val), 'FM00'),
      ':', to_char(date_part('seconds', val), 'FM00.0999999999'), 'Z'
    )
  ELSE
    concat(
      to_char(date_part('hour', val), 'FM00'), ':', to_char(date_part('minute', val), 'FM00'),
      ':', to_char(date_part('seconds', val), 'FM00.0999999999'),
      to_char(date_part('timezone_hour', val), 'S00'), ':',
      ltrim(to_char(date_part('timezone_minute', val), '00'), '+- ')
    )
END;
$$;


--
-- Name: get_attnum(oid, text); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.get_attnum(rel_id oid, att_name text) RETURNS smallint
    LANGUAGE sql STRICT
    AS $$/*
Get the attnum for a given attribute in the relation. Returns null if no such attribute exists.

Usually, this will be used to get the attnum for a column of a table.

Args:
  rel_id: The relation where we'll look for the attribute.
  att_name: The name of the attribute, unquoted.
*/
SELECT attnum FROM pg_catalog.pg_attribute WHERE attrelid=rel_id AND attname=att_name;
$$;


--
-- Name: get_cast_function_name(regtype); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.get_cast_function_name(target_type regtype) RETURNS text
    LANGUAGE plpgsql STRICT
    AS $$/*
Return a string giving the appropriate name of the casting function for the target_type.

Currently set up to duplicate the logic in our python casting function builder. This will be
changed. Given a qualified, potentially capitalized type name, we
- Remove the namespace (schema),
- Replace any white space in the type name with underscores,
- Replace double quotes in the type name (e.g., the "char" type) with '_double_quote_'
- Use the prepped type name in the name `msar.cast_to_%s`.

Args:
  target_type: This should be a type that exists.
*/
DECLARE target_type_prepped text;
BEGIN
  -- TODO: Come up with a way to build these names that is more robust against collisions.
  WITH unqualifier AS (
    SELECT x[array_upper(x, 1)] unqualified_type
    FROM regexp_split_to_array(target_type::text, '\.') x
  ), unspacer AS(
    SELECT replace(unqualified_type, ' ', '_') unspaced_type
    FROM unqualifier
  )
  SELECT replace(unspaced_type, '"', '_double_quote_')
  FROM unspacer
  INTO target_type_prepped;
  RETURN format('msar.cast_to_%s', target_type_prepped);
END;
$$;


--
-- Name: get_column_info(regclass); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.get_column_info(tab_id regclass) RETURNS jsonb
    LANGUAGE sql STRICT
    AS $$/*
Given a table identifier, return an array of objects describing the columns of the table.

Each returned JSON object in the array will have the form:
  {
    "id": <int>,
    "name": <str>,
    "type": <str>,
    "type_options": <obj>,
    "nullable": <bool>,
    "primary_key": <bool>,
    "default": {"value": <str>, "is_dynamic": <bool>},
    "has_dependents": <bool>,
    "description": <str>,
    "current_role_priv": [<str>, <str>, ...]
  }

The `type_options` object is described in the docstring of `msar.get_type_options`. The `default`
object has the keys:
  value: A string giving the value (as an SQL expression) of the default.
  is_dynamic: A boolean giving whether the default is (likely to be) dynamic.
*/
SELECT coalesce(jsonb_agg(column_data ORDER BY column_data.id ASC), '[]'::jsonb)
FROM msar.column_info_table(tab_id) AS column_data;
$$;


--
-- Name: get_column_name(oid, integer); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.get_column_name(rel_id oid, col_id integer) RETURNS text
    LANGUAGE sql STRICT
    AS $$/*
Return the UNQUOTED name for a given column in a given relation (e.g., table).

More precisely, this function returns the name of attributes that are not dropped, for any relation appearing in the
pg_class catalog table (so you could find attributes of indices with this function).

Args:
  rel_id:  The OID of the relation.
  col_id:  The attnum of the column in the relation.
*/
SELECT attname::text FROM pg_catalog.pg_attribute WHERE attrelid=rel_id AND attnum=col_id AND NOT attisdropped;
$$;


--
-- Name: get_column_name(oid, text); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.get_column_name(rel_id oid, col_name text) RETURNS text
    LANGUAGE sql STRICT
    AS $$/*
Return the UNQUOTED name for a given column in a given relation (e.g., table).

More precisely, this function returns the unquoted name of attributes that are not dropped, for any relation appearing in the
pg_class catalog table (so you could find attributes of indices with this function). If the given
col_name is not in the relation, we return null.

This has the effect of both quoting and preparing the given col_name, and also validating that it
exists.

Args:
  rel_id:  The OID of the relation.
  col_name:  The unquoted name of the column in the relation.
*/
SELECT attname::text FROM pg_catalog.pg_attribute WHERE attrelid=rel_id AND attname=col_name AND NOT attisdropped;
$$;


--
-- Name: get_column_names(oid, jsonb); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.get_column_names(rel_id oid, columns jsonb) RETURNS text[]
    LANGUAGE sql STRICT
    AS $$/*
Return the QUOTED names for given columns in a given relation (e.g., table).

- If the rel_id is given as 0, the assumption is that this is a new table, so we just apply normal
quoting rules to a column without validating anything further.
- If the rel_id is given as nonzero, and a column is given as text, then we validate that
  the column name exists in the table, and use that.
- If the rel_id is given as nonzero, and the column is given as a number, then we look the column up
  by attnum and use that name.

The columns jsonb can have a mix of numerical IDs and column names. The reason for this is that we
may be adding a column algorithmically, and this saves having to modify the column adding logic
based on the IDs passed by the user for given columns.

Args:
  rel_id:  The OID of the relation.
  columns:  A JSONB array of the unquoted names or IDs (can be mixed) of the columns.
*/
SELECT array_agg(
  CASE
    WHEN rel_id=0 THEN quote_ident(col #>> '{}')
    WHEN jsonb_typeof(col)='number' THEN quote_ident(msar.get_column_name(rel_id, col::integer))
    WHEN jsonb_typeof(col)='string' THEN quote_ident(msar.get_column_name(rel_id, col #>> '{}'))
  END
)
FROM jsonb_array_elements(columns) AS x(col);
$$;


--
-- Name: get_column_type(oid, smallint); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.get_column_type(rel_id oid, col_id smallint) RETURNS text
    LANGUAGE sql STRICT
    AS $$/*
Return the type of a given column in a relation.

Args:
  rel_id: The OID of the relation.
  col_id: The attnum of the column in the relation.
*/
SELECT atttypid::regtype
FROM pg_catalog.pg_attribute
WHERE attnum = col_id
AND attrelid = rel_id;
$$;


--
-- Name: get_constraint_name(oid); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.get_constraint_name(con_id oid) RETURNS text
    LANGUAGE plpgsql STRICT
    AS $$/*
Return the UNQUOTED constraint name of the corresponding constraint oid.

Args:
  con_id: The OID of the constraint.
*/
BEGIN
  RETURN conname::text FROM pg_catalog.pg_constraint WHERE pg_constraint.oid = con_id;
END;
$$;


--
-- Name: get_constraint_type_api_code(character); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.get_constraint_type_api_code(contype character) RETURNS text
    LANGUAGE sql
    AS $$/*
This function returns a string that represents the constraint type code used to describe
constraints when listing them within the Mathesar API.

PostgreSQL constraint types are documented by the `contype` field here:
https://www.postgresql.org/docs/current/catalog-pg-constraint.html

Notably, we don't include 't' (trigger) because triggers a bit different structurally and we don't
support working with them (yet?) in Mathesar.
*/
SELECT CASE contype
  WHEN 'c' THEN 'check'
  WHEN 'f' THEN 'foreignkey'
  WHEN 'p' THEN 'primary'
  WHEN 'u' THEN 'unique'
  WHEN 'x' THEN 'exclude'
END;
$$;


--
-- Name: get_constraints_for_table(oid); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.get_constraints_for_table(tab_id oid) RETURNS TABLE(oid oid, name text, type text, columns smallint[], referent_table_oid oid, referent_columns smallint[])
    LANGUAGE sql
    AS $$/*
Return data describing the constraints set on a given table.

Args:
  tab_id: The OID of the table.
*/
WITH constraints AS (
  SELECT
    oid,
    conname AS name,
    msar.get_constraint_type_api_code(contype::char) AS type,
    conkey AS columns,
    confrelid AS referent_table_oid,
    confkey AS referent_columns
  FROM pg_catalog.pg_constraint
  WHERE conrelid = tab_id
)
SELECT *
FROM constraints
-- Only return constraints with types that we're able to classify
WHERE type IS NOT NULL
$$;


--
-- Name: get_current_database_info(); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.get_current_database_info() RETURNS jsonb
    LANGUAGE sql STABLE STRICT
    AS $$/*
Return information about the current database.

The returned JSON object has the form:
  {
    "oid": <bigint>,
    "name": <str>,
    "owner_oid": <bigint>,
    "current_role_priv": [<str>],
    "current_role_owner": <bool>
  }
*/
SELECT jsonb_build_object(
  'oid', pgd.oid::bigint,
  'name', pgd.datname,
  'owner_oid', pgd.datdba::bigint,
  'current_role_priv', msar.list_database_privileges_for_current_role(pgd.oid),
  'current_role_owns', pg_catalog.pg_has_role(pgd.datdba, 'USAGE')
) FROM pg_catalog.pg_database AS pgd
WHERE pgd.datname = pg_catalog.current_database();
$$;


--
-- Name: get_current_role(); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.get_current_role() RETURNS jsonb
    LANGUAGE sql STABLE
    AS $$/*
Returns a JSON object describing the current_role and the parent role(s) whose
privileges are immediately available to current_role without doing SET ROLE.
*/
SELECT jsonb_build_object(
  'current_role', msar.get_role(current_role),
  'parent_roles', COALESCE(array_remove(
    array_agg(
      CASE WHEN pg_catalog.pg_has_role(current_role, role_data.name, 'USAGE')
      THEN msar.get_role(role_data.name) END
    ), NULL
  ), ARRAY[]::jsonb[])
)
FROM msar.role_info_table() AS role_data
WHERE role_data.name NOT LIKE 'pg_%'
AND role_data.name != current_role;
$$;


--
-- Name: get_database_name(oid); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.get_database_name(dat_id oid) RETURNS text
    LANGUAGE plpgsql STABLE STRICT
    AS $$/*
Return the UNQUOTED name of a given database.

If the database does not exist, an exception will be raised.

Args:
  dat_id: The OID of the role.
*/
DECLARE dat_name text;
BEGIN
  SELECT datname INTO dat_name FROM pg_catalog.pg_database WHERE oid=dat_id;

  IF dat_name IS NULL THEN
    RAISE EXCEPTION 'Database with OID % does not exist', dat_id
    USING ERRCODE = '42704'; -- undefined_object
  END IF;

  RETURN dat_name;
END;
$$;


--
-- Name: get_default_summary_column(oid); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.get_default_summary_column(tab_id oid) RETURNS smallint
    LANGUAGE sql STABLE STRICT
    AS $$/*
Choose a column to use for summarizing rows of a table.

If a string type column exists, we choose the one with a minimal attnum. If no such column exists,
we just return the column (of any type) with minimum attnum.

Only columns to which the user has access are returned.

Args:
  tab_id: The OID of the table for which we're finding a good summary column
*/
SELECT attnum
FROM pg_catalog.pg_attribute pga JOIN pg_catalog.pg_type pgt ON pga.atttypid = pgt.oid
WHERE pga.attrelid = tab_id
  AND pga.attnum > 0
  AND NOT pga.attisdropped
  AND has_column_privilege(pga.attrelid, pga.attnum, 'SELECT')
ORDER BY (CASE WHEN pgt.typcategory='S' THEN 0 ELSE 1 END), pga.attnum
LIMIT 1;
$$;


--
-- Name: get_extracted_col_def_jsonb(oid, integer[]); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.get_extracted_col_def_jsonb(tab_id oid, col_ids integer[]) RETURNS jsonb
    LANGUAGE sql STRICT
    AS $$/*
Get a JSON array of column definitions from given columns for creation of an extracted table.

See the __msar.process_col_def_jsonb for a description of the JSON.

Args:
  tab_id: The OID of the table containing the columns whose definitions we want.
  col_ids: The attnum of the columns whose definitions we want.
*/

SELECT jsonb_agg(
  jsonb_build_object(
    'name', attname,
    'type', jsonb_build_object('id', atttypid, 'modifier', atttypmod),
    'not_null', attnotnull,
    'default',
    -- We only copy non-dynamic default expressions to new table to avoid double-use of sequences.
    -- Sequences are owned by a specific column, and can't be reused without error.
    CASE WHEN NOT msar.is_default_possibly_dynamic(tab_id, col_id) THEN
      pg_catalog.pg_get_expr(adbin, tab_id)
    END
  )
)
FROM pg_catalog.pg_attribute AS pg_columns
  JOIN unnest(col_ids) AS columns_to_copy(col_id)
    ON pg_columns.attnum=columns_to_copy.col_id
  LEFT JOIN pg_catalog.pg_attrdef AS pg_column_defaults
    ON pg_column_defaults.adnum=pg_columns.attnum AND pg_columns.attrelid=pg_column_defaults.adrelid
WHERE pg_columns.attrelid=tab_id AND NOT msar.is_pkey_col(tab_id, col_id);
$$;


--
-- Name: get_extracted_con_def_jsonb(oid, integer[]); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.get_extracted_con_def_jsonb(tab_id oid, col_ids integer[]) RETURNS jsonb
    LANGUAGE sql STRICT
    AS $$/*
Get a JSON array of constraint definitions from given columns for creation of an extracted table.

See the __msar.process_con_def_jsonb for a description of the JSON.

Args:
  tab_id: The OID of the table containing the constraints whose definitions we want.
  col_ids: The attnum of columns with the constraints whose definitions we want.
*/

SELECT jsonb_agg(
  jsonb_build_object(
    'type', contype,
    'columns', ARRAY[attname],
    'deferrable', condeferrable,
    'fkey_relation_id', confrelid::bigint,
    'fkey_columns', coalesce(confkey, ARRAY[]::smallint[]),
    'fkey_update_action', confupdtype,
    'fkey_delete_action', confdeltype,
    'fkey_match_type', confmatchtype
  )
)
FROM pg_catalog.pg_constraint
  JOIN unnest(col_ids) AS columns_to_copy(col_id) ON pg_constraint.conkey[1]=columns_to_copy.col_id
  JOIN pg_catalog.pg_attribute
    ON pg_attribute.attnum=columns_to_copy.col_id AND pg_attribute.attrelid=pg_constraint.conrelid
WHERE pg_constraint.conrelid=tab_id AND (pg_constraint.contype='f' OR pg_constraint.contype='u');
$$;


--
-- Name: get_fkey_action_from_char("char"); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.get_fkey_action_from_char("char") RETURNS text
    LANGUAGE sql STRICT
    AS $_$/*
Map the "char" from pg_constraint to the update or delete action string.
*/
SELECT CASE
  WHEN $1 = 'a' THEN 'NO ACTION'
  WHEN $1 = 'r' THEN 'RESTRICT'
  WHEN $1 = 'c' THEN 'CASCADE'
  WHEN $1 = 'n' THEN 'SET NULL'
  WHEN $1 = 'd' THEN 'SET DEFAULT'
END;
$_$;


--
-- Name: get_fkey_map_table(oid); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.get_fkey_map_table(tab_id oid) RETURNS TABLE(target_oid oid, conkey smallint, confkey smallint)
    LANGUAGE sql STABLE STRICT
    AS $$/*
Generate a table mapping foreign key values from refererrer to referent tables.

Given an input table (identified by OID), we return a table with each row representing a foreign key
constraint on that table. We return only single-column foreign keys, and only one per foreign key
column.

Args:
  tab_id: The OID of the table containing the foreign key columns to map.
*/
SELECT DISTINCT ON (conkey) pgc.confrelid AS target_oid, x.conkey AS conkey, y.confkey AS confkey
FROM pg_catalog.pg_constraint pgc, LATERAL unnest(conkey) x(conkey), LATERAL unnest(confkey) y(confkey)
WHERE
  pgc.conrelid = tab_id
  AND pgc.contype='f'
  AND cardinality(pgc.confkey) = 1
  AND has_column_privilege(tab_id, x.conkey, 'SELECT')
  AND has_column_privilege(pgc.confrelid, y.confkey, 'SELECT')
ORDER BY conkey, target_oid, confkey;
$$;


--
-- Name: get_fkey_match_type_from_char("char"); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.get_fkey_match_type_from_char("char") RETURNS text
    LANGUAGE sql STRICT
    AS $_$/*
Convert a char to its proper string describing the match type.

NOTE: Since 'PARTIAL' is not implemented (and throws an error), we don't use it here.
*/
SELECT CASE
  WHEN $1 = 'f' THEN 'FULL'
  WHEN $1 = 's' THEN 'SIMPLE'
END;
$_$;


--
-- Name: get_interval_fields(integer); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.get_interval_fields(typ_mod integer) RETURNS text
    LANGUAGE sql STRICT
    AS $$/*
Return the string giving the fields for an interval typmod integer.

This logic is ported from the relevant PostgreSQL source code, reimplemented in SQL. See the
`intervaltypmodout` function at
https://doxygen.postgresql.org/backend_2utils_2adt_2timestamp_8c.html

Args:
  typ_mod: The atttypmod from the pg_attribute table. Should be valid for the interval type.
*/
SELECT CASE (typ_mod >> 16 & 32767)
  WHEN 1 << 2 THEN 'year'
  WHEN 1 << 1 THEN 'month'
  WHEN 1 << 3 THEN 'day'
  WHEN 1 << 10 THEN 'hour'
  WHEN 1 << 11 THEN 'minute'
  WHEN 1 << 12 THEN 'second'
  WHEN (1 << 2) | (1 << 1) THEN 'year to month'
  WHEN (1 << 3) | (1 << 10) THEN 'day to hour'
  WHEN (1 << 3) | (1 << 10) | (1 << 11) THEN 'day to minute'
  WHEN (1 << 3) | (1 << 10) | (1 << 11) | (1 << 12) THEN 'day to second'
  WHEN (1 << 10) | (1 << 11) THEN 'hour to minute'
  WHEN (1 << 10) | (1 << 11) | (1 << 12) THEN 'hour to second'
  WHEN (1 << 11) | (1 << 12) THEN 'minute to second'
END;
$$;


--
-- Name: get_joinable_tables(integer); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.get_joinable_tables(max_depth integer) RETURNS SETOF msar.joinable_tables
    LANGUAGE sql STABLE
    AS $$/*
This function returns a table of msar.joinable_tables objects, giving paths to various
joinable tables.

Args:
  max_depth: This controls how far to search for joinable tables.

The base and target are OIDs of a base table, and a target table that can be
joined by some combination of joins along single-column foreign key column
restrictions in either way.
*/
WITH RECURSIVE symmetric_fkeys AS (
  SELECT
    c.oid::BIGINT fkey_oid,
    c.conrelid::BIGINT left_rel,
    c.confrelid::BIGINT right_rel,
    c.conkey[1]::INTEGER left_col,
    c.confkey[1]::INTEGER right_col,
    false multiple_results,
    false reversed
  FROM pg_catalog.pg_constraint c
  WHERE c.contype='f' and array_length(c.conkey, 1)=1
UNION ALL
  SELECT
    c.oid::BIGINT fkey_oid,
    c.confrelid::BIGINT left_rel,
    c.conrelid::BIGINT right_rel,
    c.confkey[1]::INTEGER left_col,
    c.conkey[1]::INTEGER right_col,
    true multiple_results,
    true reversed
  FROM pg_catalog.pg_constraint c
  WHERE c.contype='f' and array_length(c.conkey, 1)=1
),

search_fkey_graph(
    left_rel, right_rel, left_col, right_col, depth, join_path, fkey_path, multiple_results
) AS (
  SELECT
    sfk.left_rel,
    sfk.right_rel,
    sfk.left_col,
    sfk.right_col,
    1,
    jsonb_build_array(
      jsonb_build_array(
        jsonb_build_array(sfk.left_rel, sfk.left_col),
        jsonb_build_array(sfk.right_rel, sfk.right_col)
      )
    ),
    jsonb_build_array(jsonb_build_array(sfk.fkey_oid, sfk.reversed)),
    sfk.multiple_results
  FROM symmetric_fkeys sfk
UNION ALL
  SELECT
    sfk.left_rel,
    sfk.right_rel,
    sfk.left_col,
    sfk.right_col,
    sg.depth + 1,
    join_path || jsonb_build_array(
      jsonb_build_array(
        jsonb_build_array(sfk.left_rel, sfk.left_col),
        jsonb_build_array(sfk.right_rel, sfk.right_col)
      )
    ),
    fkey_path || jsonb_build_array(jsonb_build_array(sfk.fkey_oid, sfk.reversed)),
    sg.multiple_results OR sfk.multiple_results
  FROM symmetric_fkeys sfk, search_fkey_graph sg
  WHERE
    sfk.left_rel=sg.right_rel
    AND depth<max_depth
    AND (join_path -> -1) != jsonb_build_array(
      jsonb_build_array(sfk.right_rel, sfk.right_col),
      jsonb_build_array(sfk.left_rel, sfk.left_col)
    )
), output_cte AS (
  SELECT
    (join_path#>'{0, 0, 0}')::INTEGER base,
    (join_path#>'{-1, -1, 0}')::INTEGER target,
    join_path,
    fkey_path,
    depth,
    multiple_results
  FROM search_fkey_graph
)
SELECT * FROM output_cte;
$$;


--
-- Name: get_joinable_tables(integer, oid); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.get_joinable_tables(max_depth integer, table_id oid) RETURNS jsonb
    LANGUAGE sql STABLE STRICT
    AS $$
  WITH jt_cte AS (
    SELECT * FROM msar.get_joinable_tables(max_depth) WHERE base=table_id
  ), target_cte AS (
    SELECT pga.attrelid AS tt_oid, 
      jsonb_build_object(
        'name', msar.get_relation_name(pga.attrelid),
        'columns', jsonb_object_agg(
            pga.attnum, jsonb_build_object(
              'name', pga.attname,
              'type', CASE WHEN attndims>0 THEN '_array' ELSE atttypid::regtype::text END,
              'primary_key', msar.is_pkey_col(pga.attrelid, pga.attnum)
            )
          )
      ) AS tt_info
    FROM pg_catalog.pg_attribute AS pga, jt_cte
    WHERE pga.attrelid=jt_cte.target AND pga.attnum > 0 and NOT pga.attisdropped
    GROUP BY pga.attrelid
  ), joinable_tables AS (
    SELECT jsonb_agg(to_jsonb(jt_cte.*)) AS jt FROM jt_cte
  ), target_table_info AS (
    SELECT jsonb_object_agg(tt_oid, tt_info) AS tt FROM target_cte
  )
  SELECT jsonb_build_object(
    'joinable_tables', COALESCE(joinable_tables.jt, '[]'::jsonb),
    'target_table_info', COALESCE(target_table_info.tt, '{}'::jsonb)
  ) FROM joinable_tables, target_table_info;
$$;


--
-- Name: get_joined_columns_expr_json(jsonb); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.get_joined_columns_expr_json(joined_columns jsonb) RETURNS jsonb
    LANGUAGE sql STABLE STRICT
    AS $_$/*
Returns a json object containing SQL exprs essential for listing aggregates of pk-ids for a table
which is connect via a simple many-to-many relation.

joined_columns should have the folowing form:
[
  {"alias": "column_alias_1", "join_path": [[[17837, 1],[17842, 2]], [[17842, 3],[17820, 1]]]},
  {"alias": "column_alias_2", "join_path": [[[17837, 1], [17847, 2]], [[17847, 3], [17874, 1]]]},
]

Args:
  joined_columns: A list of JSON object that include an "alias" and "join_path" where,
    "join_path" represents linkages via a simple many-to-many mapping to a column in another table.
*/
  WITH cte AS (
    SELECT
      t.alias AS alias,
      msar.get_relation_name((t.join_path->0->0->>0)::oid) AS base_tab_name,
      msar.get_column_name((t.join_path->0->0->>0)::oid, (t.join_path->0->0->>1)::int) AS base_tab_col_name,
      msar.get_relation_name((t.join_path->-1->-1->>0)::oid) AS target_tab_name,
      msar.get_column_name((t.join_path->-1->-1->>0)::oid, (t.join_path->-1->-1->>1)::int) AS target_tab_col_name,
      msar.build_join_expr(t.join_path) AS join_expr
    FROM ROWS FROM (
      jsonb_to_recordset(joined_columns) AS (
        alias text,
        join_path jsonb
      )
    ) WITH ORDINALITY AS t(alias, join_path)
    ORDER BY ordinality
  ) SELECT jsonb_build_object(
      'selectable_joined_columns_expr', string_agg(
        format(
          $q$
          jsonb_build_object(
            'count', COUNT(DISTINCT %1$I.%2$I),
            'result', jsonb_path_query_array(
              COALESCE(
                NULLIF(
                  jsonb_agg(DISTINCT %1$I.%2$I),
                  '[null]'::jsonb
                ),
                '[]'::jsonb
              ), '$[0 to 24]'
            ) -- limit results to 25
          ) AS %3$I
          $q$,
          cte.target_tab_name,
          cte.target_tab_col_name,
          cte.alias
        ),
        ', '
      ),
      'join_sql_expr', string_agg(
        cte.join_expr,
        E'\n'
      ),
      'join_group_by_expr', 'GROUP BY ' || string_agg(
        DISTINCT format(
          '%I.%I',
          base_tab_name,
          base_tab_col_name
        ),
        ', '
      )
  ) FROM cte
$_$;


--
-- Name: get_mathesar_money_array(text); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.get_mathesar_money_array(text) RETURNS text[]
    LANGUAGE plpgsql
    AS $_$
DECLARE
  raw_arr text[];
  actual_number_arr text[];
  group_divider_arr text[];
  decimal_point_arr text[];
  actual_number text;
  group_divider text;
  decimal_point text;
  currency_prefix text;
  currency_suffix text;

  -- pieces required for regex
  non_numeric text := '(?:[^.,0-9]+)';
  no_separator_big text := '[0-9]{4,}(?:([,.])[0-9]+)?';
  no_separator_small text := '[0-9]{1,3}(?:([,.])[0-9]{1,2}|[0-9]{4,})?';
  comma_separator_req_decimal text := '[0-9]{1,3}(,)[0-9]{3}(\.)[0-9]+';
  period_separator_req_decimal text := '[0-9]{1,3}(\.)[0-9]{3}(,)[0-9]+';
  comma_separator_opt_decimal text := '[0-9]{1,3}(?:(,)[0-9]{3}){2,}(?:(\.)[0-9]+)?';
  period_separator_opt_decimal text := '[0-9]{1,3}(?:(\.)[0-9]{3}){2,}(?:(,)[0-9]+)?';
  space_separator_opt_decimal text := '[0-9]{1,3}(?:( )[0-9]{3})+(?:([,.])[0-9]+)?';
  comma_separator_lakh_system text := '[0-9]{1,2}(?:(,)[0-9]{2})+,[0-9]{3}(?:(\.)[0-9]+)?';
  inner_number_tree text := concat_ws('|',
                            no_separator_big,
                            no_separator_small,
                            comma_separator_req_decimal,
                            period_separator_req_decimal,
                            comma_separator_opt_decimal,
                            period_separator_opt_decimal,
                            space_separator_opt_decimal,
                            comma_separator_lakh_system
                          );
  inner_number_group text := '(' || inner_number_tree || ')';/* Numbers without currency,
  including inner_number_group as its own capturing group in the money_finding_regex allows us to
  match columns without any currency symbols allowing users the flexibility
  to add currency prefixes and suffixes afterwards in the cases of type inference/data imports. */
  required_currency_beginning text := non_numeric || inner_number_group || non_numeric || '?';
  required_currency_ending text := non_numeric || '?' || inner_number_group || non_numeric;
  money_finding_regex text := '^(?:' || required_currency_beginning || '|' || required_currency_ending || '|' || inner_number_group || ')$';
BEGIN
  SELECT regexp_matches($1, money_finding_regex) INTO raw_arr;
  IF raw_arr IS NULL THEN
    RETURN NULL;
  END IF;
  SELECT array_remove(ARRAY[
    raw_arr[1], -- required_currency_beginning[x]
    raw_arr[16], -- required_currency_ending[x+15]
    raw_arr[31] -- inner_number_group[x+30]
  ], null) INTO actual_number_arr;
  SELECT array_remove(ARRAY[
    raw_arr[4],raw_arr[6],raw_arr[8],raw_arr[10],raw_arr[12],raw_arr[14], -- required_currency_beginning[x]
    raw_arr[19],raw_arr[21],raw_arr[23],raw_arr[25],raw_arr[27],raw_arr[29], -- required_currency_ending[x+15]
    raw_arr[34],raw_arr[36],raw_arr[38],raw_arr[40],raw_arr[42],raw_arr[44] -- inner_number_group[x+30]
  ], null) INTO group_divider_arr;
  SELECT array_remove(ARRAY[
    raw_arr[2],raw_arr[3],raw_arr[5],raw_arr[7],raw_arr[9],raw_arr[11],raw_arr[13],raw_arr[15], -- required_currency_beginning[x]
    raw_arr[17],raw_arr[18],raw_arr[20],raw_arr[22],raw_arr[24],raw_arr[26],raw_arr[28],raw_arr[30], -- required_currency_ending[x+15]
    raw_arr[32],raw_arr[33],raw_arr[35],raw_arr[37],raw_arr[39],raw_arr[41],raw_arr[43],raw_arr[45] -- inner_number_group[x+30]
  ], null) INTO decimal_point_arr;
  SELECT actual_number_arr[1] INTO actual_number;
  SELECT group_divider_arr[1] INTO group_divider;
  SELECT decimal_point_arr[1] INTO decimal_point;
  SELECT replace(replace(split_part($1, actual_number, 1), '-', ''), '(', '') INTO currency_prefix;
  SELECT replace(replace(split_part($1, actual_number, 2), '-', ''), ')', '') INTO currency_suffix;
  IF $1::text ~ '^.*(-|\(.+\)).*$' THEN -- Handle negative values
    actual_number := '-' || actual_number;
  END IF;
  RETURN ARRAY[actual_number, group_divider, decimal_point, currency_prefix, currency_suffix];
END;
$_$;


--
-- Name: get_numeric_array(text); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.get_numeric_array(text) RETURNS text[]
    LANGUAGE plpgsql
    AS $_$
  DECLARE
    raw_arr text[];
    actual_number_arr text[];
    group_divider_arr text[];
    decimal_point_arr text[];
    actual_number text;
    group_divider text;
    decimal_point text;
  BEGIN
    SELECT regexp_matches($1, '^(?:[+-]?([0-9]{4,}(?:([,.])[0-9]+)?|[0-9]{1,3}(?:([,.])[0-9]{1,2}|[0-9]{4,})?|[0-9]{1,3}(,)[0-9]{3}(\.)[0-9]+|[0-9]{1,3}(\.)[0-9]{3}(,)[0-9]+|[0-9]{1,3}(?:(,)[0-9]{3}){2,}(?:(\.)[0-9]+)?|[0-9]{1,3}(?:(\.)[0-9]{3}){2,}(?:(,)[0-9]+)?|[0-9]{1,3}(?:( )[0-9]{3})+(?:([,.])[0-9]+)?|[0-9]{1,2}(?:(,)[0-9]{2})+,[0-9]{3}(?:(\.)[0-9]+)?|[0-9]{1,3}(?:(\'')[0-9]{3})+(?:([.])[0-9]+)?))$') INTO raw_arr;
    IF raw_arr IS NULL THEN
      RETURN NULL;
    END IF;
    SELECT array_remove(ARRAY[raw_arr[1]], null) INTO actual_number_arr;
    SELECT array_remove(ARRAY[raw_arr[4],raw_arr[6],raw_arr[8],raw_arr[10],raw_arr[12],raw_arr[14],raw_arr[16]], null) INTO group_divider_arr;
    SELECT array_remove(ARRAY[raw_arr[2],raw_arr[3],raw_arr[5],raw_arr[7],raw_arr[9],raw_arr[11],raw_arr[13],raw_arr[15],raw_arr[17]], null) INTO decimal_point_arr;
    SELECT actual_number_arr[1] INTO actual_number;
    SELECT group_divider_arr[1] INTO group_divider;
    SELECT decimal_point_arr[1] INTO decimal_point;
    RETURN ARRAY[actual_number, group_divider, decimal_point];
  END;
$_$;


--
-- Name: get_object_counts(); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.get_object_counts() RETURNS jsonb
    LANGUAGE sql STABLE
    AS $$/*
Return a JSON object with counts of some objects in the database.

We exclude the mathesar-system schemas.

The objects counted are:
- total schemas, excluding Mathesar internal schemas
- total tables in the included schemas
- total rows of tables included
*/
SELECT jsonb_build_object(
  'schema_count', COUNT(DISTINCT pgn.oid),
  'table_count', COUNT(pgc.oid),
  'record_count', SUM(pgc.reltuples)
)
FROM pg_catalog.pg_namespace pgn
LEFT JOIN pg_catalog.pg_class pgc ON pgc.relnamespace = pgn.oid AND pgc.relkind = 'r'
WHERE pgn.nspname <> 'information_schema'
AND NOT (pgn.nspname = ANY(msar.mathesar_system_schemas()))
AND pgn.nspname NOT LIKE 'pg_%';
$$;


--
-- Name: get_other_column_ids(regclass, smallint[]); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.get_other_column_ids(tab_id regclass, col_ids smallint[]) RETURNS smallint[]
    LANGUAGE sql STABLE STRICT
    AS $$
SELECT array_agg(attnum)
FROM pg_catalog.pg_attribute
WHERE
  attrelid = tab_id
  AND attnum > 0
  AND NOT attisdropped
  AND attnum <> all(col_ids);
$$;


--
-- Name: get_pk_column(oid); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.get_pk_column(rel_id oid) RETURNS smallint
    LANGUAGE sql STRICT
    AS $$/*
Return the first column attnum in the primary key of a given relation (e.g., table).

TODO: resolve potential code duplication between this function and `get_selectable_pkey_attnum`.

Args:
  rel_id: The OID of the relation.
*/
SELECT CASE WHEN array_length(conkey, 1) = 1 THEN conkey[1] END
FROM pg_catalog.pg_constraint
WHERE contype='p'
AND conrelid=rel_id;
$$;


--
-- Name: get_pkey_order(oid); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.get_pkey_order(tab_id oid) RETURNS jsonb
    LANGUAGE sql STABLE STRICT
    AS $$
SELECT jsonb_agg(jsonb_build_object('attnum', attnum, 'direction', 'asc'))
FROM pg_catalog.pg_constraint, LATERAL unnest(conkey) attnum
WHERE contype='p' AND conrelid=tab_id AND has_column_privilege(tab_id, attnum, 'SELECT');
$$;


--
-- Name: get_preview(oid, jsonb, integer); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.get_preview(tab_id oid, col_cast_def jsonb, rec_limit integer) RETURNS jsonb
    LANGUAGE plpgsql
    AS $$/*
Preview a table, applying different type casts and options to the underlying columns before import,
returning a JSON object describing the records of the table.

Note that these casts are temporary and do not alter the data in the underlying table,
if you wish to alter these settings permanantly for the columns see msar.alter_columns.

Args:
  tab_id: The OID of the table to preview.
  col_cast_def: A JSON object describing the column settings to apply.
  rec_limit (optional): The upper limit for the number of records to return.

The col_cast_def JSONB should have the form:
[
  {
    "attnum": <int>,
    "type": {
      "name": <str>,
      "options": {
        "length": <integer>,
        "precision": <integer>,
        "scale": <integer>
        "fields": <str>,
        "array": <boolean>
      }
    },
  },
  {
    ...
  },
  ...
]
*/
DECLARE
  tab_name text;
  sel_query text;
  records jsonb;
BEGIN
  tab_name := __msar.get_qualified_relation_name(tab_id);
  sel_query := 'SELECT %s FROM %s LIMIT %L';
  WITH preview_cte AS (
    SELECT string_agg(
      'CAST(' ||
      msar.build_cast_expr(
        quote_ident(msar.get_column_name(tab_id, (col_cast ->> 'attnum')::integer)),
        col_cast -> 'type' ->> 'name',
        coalesce(col_cast -> 'cast_options', '{}'::jsonb)
      ) ||
      ' AS ' ||
      msar.build_type_text(col_cast -> 'type') ||
      ')'|| ' AS ' || quote_ident(msar.get_column_name(tab_id, (col_cast ->> 'attnum')::integer)),
      ', '
    ) AS cast_expr
    FROM jsonb_array_elements(col_cast_def) AS col_cast
  )
  SELECT
    __msar.exec_dql(sel_query, cast_expr, tab_name, rec_limit::text)
  INTO records FROM preview_cte;
  RETURN records;
END;
$$;


--
-- Name: get_record_from_table(oid, anycompatible, jsonb, boolean, jsonb); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.get_record_from_table(tab_id oid, rec_id anycompatible, joined_columns jsonb DEFAULT NULL::jsonb, return_record_summaries boolean DEFAULT false, table_record_summary_templates jsonb DEFAULT NULL::jsonb) RETURNS jsonb
    LANGUAGE sql STABLE
    AS $$/*
Get single record from a table. Only columns to which the user has access are returned.

Args:
  tab_id: The OID of the table whose record we'll get.
  rec_id: The id value of the record.
  joined_columns: (optional) A jsonb list defining columns joined via a simple many-to-many linkage.
    See msar.get_joined_columns_expr_json for more details.
  return_record_summaries : Whether to return a summary for the record listed.
  table_record_summary_templates: A JSON object that maps table OIDs to record summary
    templates.

The table must have a single primary key column.
*/
SELECT msar.list_records_from_table(
  tab_id,
  null,
  null,
  null,
  jsonb_build_object(
    'type', 'equal',
    'args', jsonb_build_array(
      jsonb_build_object('type', 'attnum', 'value', msar.get_pk_column(tab_id)),
      jsonb_build_object('type', 'literal', 'value', rec_id)
    )
  ),
  null,
  joined_columns,
  return_record_summaries,
  table_record_summary_templates
)
$$;


--
-- Name: get_relation_name(oid); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.get_relation_name(rel_oid oid) RETURNS text
    LANGUAGE plpgsql STRICT
    AS $$/*
Return the UNQUOTED name of a given relation (e.g., table).

If the relation does not exist, an exception will be raised.

Args:
  rel_oid: The OID of the relation.
*/
DECLARE rel_name text;
BEGIN
  SELECT relname INTO rel_name FROM pg_catalog.pg_class WHERE oid=rel_oid;

  IF rel_name IS NULL THEN
    RAISE EXCEPTION 'Relation with OID % does not exist', rel_oid
    USING ERRCODE = '42P01'; -- undefined_table
  END IF;

  RETURN rel_name;
END;
$$;


--
-- Name: get_relation_namespace_oid(oid); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.get_relation_namespace_oid(rel_id oid) RETURNS oid
    LANGUAGE sql STRICT
    AS $$/*
Get the OID of the namespace containing the given relation.

Most useful for getting the OID of the schema of a given table.

Args:
  rel_id: The OID of the relation whose namespace we want to find.
*/
SELECT relnamespace FROM pg_catalog.pg_class WHERE oid=rel_id;
$$;


--
-- Name: get_relation_schema_name(oid); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.get_relation_schema_name(rel_oid oid) RETURNS text
    LANGUAGE plpgsql STRICT
    AS $$/*
Return the UNQUOTED name of the schema which contains a given relation (e.g., table).

If the relation does not exist, an exception will be raised.

Args:
  rel_oid: The OID of the relation.
*/
DECLARE sch_name text;
BEGIN
  SELECT n.nspname INTO sch_name
  FROM pg_catalog.pg_class c
  JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
  WHERE c.oid = rel_oid;

  IF sch_name IS NULL THEN
    RAISE EXCEPTION 'Relation with OID % does not exist', rel_oid
    USING ERRCODE = '42P01'; -- undefined_table
  END IF;

  RETURN sch_name;
END;
$$;


--
-- Name: get_role(text); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.get_role(rolename text) RETURNS jsonb
    LANGUAGE sql STABLE
    AS $$/*
Given a rolename, return a JSON object describing the role in a database server.

The returned JSON object has the form:
  {
    "oid": <int>
    "name": <str>
    "super": <bool>
    "inherits": <bool>
    "create_role": <bool>
    "create_db": <bool>
    "login": <bool>
    "description": <str|null>
    "members": <[
        { "oid": <int>, "admin": <bool> }
      ]|null>
  }
*/
SELECT to_jsonb(role_data)
FROM msar.role_info_table() AS role_data
WHERE role_data.name = rolename;
$$;


--
-- Name: get_role_name(oid); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.get_role_name(rol_oid oid) RETURNS text
    LANGUAGE plpgsql STABLE STRICT
    AS $$/*
Return the UNQUOTED name of a given role.

If the role does not exist, an exception will be raised.

Args:
  rol_oid: The OID of the role.
*/
DECLARE rol_name text;
BEGIN
  SELECT rolname INTO rol_name FROM pg_catalog.pg_roles WHERE oid=rol_oid;

  IF rol_name IS NULL THEN
    RAISE EXCEPTION 'Role with OID % does not exist', rol_oid
    USING ERRCODE = '42704'; -- undefined_object
  END IF;

  RETURN rol_name;
END;
$$;


--
-- Name: get_schema(regnamespace); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.get_schema(sch_id regnamespace) RETURNS jsonb
    LANGUAGE sql STABLE
    AS $$/*
Return a json object describing the user-defined schema in the database.

Each returned JSON object will have the form:
  {
    "oid": <int>
    "name": <str>
    "description": <str|null>
    "owner_oid": <int>,
    "current_role_priv": [<str>],
    "current_role_owns": <bool>,
    "table_count": <int>
  }
*/
SELECT to_jsonb(schema_data)
FROM msar.schema_info_table() AS schema_data
WHERE schema_data.oid = sch_id;
$$;


--
-- Name: get_schema_name(oid); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.get_schema_name(sch_id oid) RETURNS text
    LANGUAGE plpgsql
    AS $$/*
Return the UNQUOTED name for a given schema.

Raises an exception if the schema is not found.

Args:
  sch_id: The OID of the schema.
*/
DECLARE sch_name text;
BEGIN
  SELECT nspname INTO sch_name FROM pg_catalog.pg_namespace WHERE oid=sch_id;

  IF sch_name IS NULL THEN
    RAISE EXCEPTION 'No schema with OID % exists.', sch_id
    USING ERRCODE = '3F000'; -- invalid_schema_name
  END IF;

  RETURN sch_name;
END;
$$;


--
-- Name: get_schema_objects_table(regnamespace[]); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.get_schema_objects_table(sch_ids regnamespace[]) RETURNS TABLE(obj_id oid, obj_schema text, obj_name text, obj_kind text)
    LANGUAGE sql STABLE STRICT
    AS $$ /*
Return a table with information about most objects in the given schemas.
*/
WITH obj_cte AS (
  (
    SELECT
      oid AS obj_id,
      msar.get_schema_name(pronamespace) AS obj_schema,
      proname AS obj_name,
      CASE prokind
        WHEN 'a' THEN 'AGGREGATE'
        WHEN 'p' THEN 'PROCEDURE'
        ELSE 'FUNCTION'
      END AS obj_kind
    FROM pg_catalog.pg_proc
    WHERE pronamespace=ANY(sch_ids)
  ) UNION (
    SELECT
      oid AS obj_id,
      msar.get_schema_name(typnamespace) AS obj_schema,
      typname AS obj_name,
      'TYPE' AS obj_kind
    FROM pg_catalog.pg_type
    WHERE typnamespace=ANY(sch_ids)
  ) UNION (
    SELECT
      oid AS obj_id,
      msar.get_schema_name(relnamespace) AS obj_schema,
      relname AS obj_name,
      CASE relkind
        WHEN 'r' THEN 'TABLE'
        WHEN 'p' THEN 'TABLE'
        WHEN 'i' THEN 'INDEX'
        WHEN 'I' THEN 'INDEX'
        WHEN 'S' THEN 'SEQUENCE'
        WHEN 'v' THEN 'VIEW'
        WHEN 'm' THEN 'MATERIALIZED VIEW'
        WHEN 'c' THEN 'TYPE'
        WHEN 'f' THEN 'FOREIGN TABLE'
      END AS obj_kind
    FROM pg_catalog.pg_class
    WHERE relnamespace=ANY(sch_ids)
  )
) SELECT DISTINCT obj_id, obj_schema, obj_name, obj_kind FROM obj_cte WHERE obj_kind IS NOT NULL;
$$;


--
-- Name: get_schema_oid(text); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.get_schema_oid(sch_name text) RETURNS oid
    LANGUAGE sql STRICT
    AS $$/*
Return the OID of a schema, or NULL if the schema does not exist.

Args :
  sch_name: The name of the schema, UNQUOTED.
*/
SELECT oid FROM pg_catalog.pg_namespace WHERE nspname=sch_name;
$$;


--
-- Name: get_score_expr(oid, jsonb); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.get_score_expr(tab_id oid, parameters_ jsonb) RETURNS text
    LANGUAGE sql STABLE STRICT
    AS $_$
SELECT string_agg(
  CASE WHEN pgt.typcategory = 'S' OR pgt.typname = 'uuid' THEN
    format(
      $s$(CASE
        WHEN %1$I::text ILIKE %2$L THEN 4
        WHEN %1$I::text ILIKE %2$L || '%%' THEN 3
        WHEN %1$I::text ILIKE '%%' || %2$L || '%%' THEN 2
        ELSE 0
      END)$s$,
      pga.attname,
      x.literal
    )
  ELSE
    format('(CASE WHEN %1$I = %2$L THEN 4 ELSE 0 END)', pga.attname, x.literal)
  END,
  ' + '
)
FROM jsonb_to_recordset(parameters_) AS x(attnum smallint, literal text)
  INNER JOIN pg_catalog.pg_attribute AS pga ON x.attnum = pga.attnum
  INNER JOIN pg_catalog.pg_type AS pgt ON pga.atttypid = pgt.oid
WHERE
  pga.attrelid = tab_id
  AND NOT pga.attisdropped
  AND has_column_privilege(tab_id, x.attnum, 'SELECT')
$_$;


--
-- Name: get_selectable_columns(oid); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.get_selectable_columns(tab_id oid) RETURNS jsonb
    LANGUAGE sql STABLE STRICT
    AS $$/*
Returns a jsonb object with the columns to which the user has access.

Given columns with attnums 2, 3, and 4, and assuming the user has access only to columns 2 and 4,
this function will return a jsonb as follows:

{ "2": <name of column with oid 2>, "4": <name of column with oid 4> }

Args:
  tab_id: The OID of the table containing the columns to select.
*/
SELECT coalesce(jsonb_object_agg(attnum, attname), '{}'::jsonb)
FROM pg_catalog.pg_attribute
WHERE
  attrelid = tab_id
  AND attnum > 0
  AND NOT attisdropped
  AND has_column_privilege(attrelid, attnum, 'SELECT');
$$;


--
-- Name: get_selectable_pkey_attnum(regclass); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.get_selectable_pkey_attnum(rel_id regclass) RETURNS smallint
    LANGUAGE sql STABLE STRICT
    AS $$/*
Get the attnum of the single-column primary key for a relation if it has one. If not, return null.

The attnum will only be returned if the current user has SELECT on that column.

TODO: resolve potential code duplication between this function and `get_pk_column`.

Args:
  rel_id:  The OID of the relation.
*/
SELECT conkey[1] FROM pg_catalog.pg_constraint
WHERE
  conrelid = rel_id
  AND cardinality(conkey) = 1
  AND contype='p'
  AND has_column_privilege(rel_id, conkey[1], 'SELECT');
$$;


--
-- Name: get_simple_mapping_join_cte(jsonb, text); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.get_simple_mapping_join_cte(join_path jsonb, record_pkey text) RETURNS text
    LANGUAGE plpgsql
    AS $_$
DECLARE
  mapping_rel regclass;
  filter_col_attnum smallint;
  join_col_attnum smallint;
BEGIN
  IF jsonb_array_length(join_path) <> 2 THEN
    RAISE EXCEPTION 'Join path wrong length';
  ELSIF join_path -> 0 -> 1 -> 0 <> join_path -> 1 -> 0 -> 0 THEN
    RAISE EXCEPTION 'Inconsistent mapping table OID';
  ELSIF join_path IS NULL OR record_pkey IS NULL THEN
    RETURN 'SELECT NULL AS join_key, NULL AS mapping_keys';
  ELSE
    mapping_rel := msar.get_simple_mapping_regclass(join_path);
    filter_col_attnum := join_path -> 0 -> 1 ->> 1;
    join_col_attnum := join_path -> 1 -> 0 ->> 1;
    RETURN format(
      $c$
        SELECT %1$I AS join_key, jsonb_agg(%2$I) AS mapping_keys
        FROM %3$I.%4$I WHERE %5$I = %6$L GROUP BY join_key
      $c$,
      /* 1 */ msar.get_column_name(mapping_rel, join_col_attnum),
      /* 2 */ msar.get_column_name(mapping_rel, msar.get_selectable_pkey_attnum(mapping_rel)),
      /* 3 */ msar.get_relation_schema_name(mapping_rel),
      /* 4 */ msar.get_relation_name(mapping_rel),
      /* 5 */ msar.get_column_name(mapping_rel, filter_col_attnum),
      /* 6 */ record_pkey
    );
  END IF;
END;
$_$;


--
-- Name: get_simple_mapping_regclass(jsonb); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.get_simple_mapping_regclass(join_path jsonb) RETURNS regclass
    LANGUAGE sql
    AS $$
  SELECT (join_path -> 0 -> 1 ->> 0)::bigint;
$$;


--
-- Name: get_tab_col_info_map(jsonb); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.get_tab_col_info_map(tab_col_map jsonb) RETURNS jsonb
    LANGUAGE sql STRICT
    AS $$/*
Returns table_info and column_info for a given tab_col_map.

tab_col_map should have the following form:
{
  "table_oid_1": [col_attnum_1, col_attnum_2, col_attnum_3],
  "table_oid_2": [col_attnum_4, col_attnum_5]
}

Returns:
{
  "table_oid_1": {
    "table_info": table_info(),
    "columns": {"col_attnum_1": col_info(), "col_attnum_2": col_info(), "col_attnum_3": col_info()
  },
  "table_oid2": {
    "table_info": table_info(),
    "columns": {"col_attnum_4": col_info(), "col_attnum_5": col_info()}
  }
}
*/
  WITH cte AS (
    SELECT
      tab_id::oid,
      ARRAY(SELECT jsonb_array_elements_text(attnums))::int[] AS attnums
    FROM jsonb_each(coalesce(tab_col_map, '{}'::jsonb)) AS x(tab_id, attnums)
  ),
  tab_info_cte AS (
    SELECT tab_id, jsonb_agg(tab_info) AS tab_info_json FROM cte
    LEFT JOIN msar.table_info_table() AS tab_info ON tab_info.oid=cte.tab_id
    GROUP BY tab_id
  ),
  col_info_cte AS (
    SELECT cte.tab_id AS tab_id,
    jsonb_object_agg(
      column_info.id, column_info
    ) AS col_info_json FROM cte
    LEFT JOIN pg_catalog.pg_attribute pga ON cte.tab_id = pga.attrelid
    LEFT JOIN msar.column_info_table(cte.tab_id) AS column_info ON pga.attnum = column_info.id
    WHERE column_info.id = ANY(cte.attnums)
    GROUP BY cte.tab_id
  )
  SELECT coalesce(
    jsonb_object_agg(
      tic.tab_id, jsonb_build_object(
      'table_info', coalesce(tic.tab_info_json,'{}'::jsonb),
      'columns', coalesce(cic.col_info_json, '{}'::jsonb)
    )
  ), '{}'::jsonb)
  FROM tab_info_cte AS tic
  LEFT JOIN col_info_cte AS cic ON tic.tab_id = cic.tab_id
$$;


--
-- Name: get_table(regclass); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.get_table(tab_id regclass) RETURNS jsonb
    LANGUAGE sql STRICT
    AS $$/*
Given a table identifier, return a JSON object describing the table.

Each returned JSON object will have the form:
  {
    "oid": <int>,
    "name": <str>,
    "schema": <int>,
    "description": <str>,
    "owner_oid": <int>,
    "current_role_priv": [<str>],
    "current_role_owns": <bool>,
    "type": <str>
  }

The "type" field will be one of: "table", "view", or "materialized_view".

Args:
  tab_id: The OID or name of the table.
*/
SELECT to_jsonb(table_data)
FROM msar.table_info_table() AS table_data
WHERE table_data.oid = tab_id;
$$;


--
-- Name: get_table_columns_and_records(oid, integer, integer, jsonb, jsonb); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.get_table_columns_and_records(tab_id oid, limit_ integer, offset_ integer, order_ jsonb, filter_ jsonb) RETURNS SETOF jsonb
    LANGUAGE plpgsql STABLE
    AS $_$
DECLARE
  expr_and_ctes jsonb;
BEGIN
  SELECT msar.build_record_list_query_components_with_ctes(
    tab_id,
    limit_,
    offset_,
    order_,
    filter_,
    null,
    null
  ) INTO expr_and_ctes;

  RETURN QUERY SELECT msar.get_selectable_columns(tab_id);
  RETURN QUERY EXECUTE format(
    $q$
    WITH results_cte AS ( %1$s )
    SELECT %2$s AS records FROM results_cte;
    $q$,
    expr_and_ctes ->> 'results_cte_query',
    COALESCE(
      msar.build_results_setof_jsonb_expr('results_cte'),
      'NULL'
    )
  );
END;
$_$;


--
-- Name: get_table_info(regnamespace); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.get_table_info(sch_id regnamespace) RETURNS jsonb
    LANGUAGE sql STRICT
    AS $$/*
Given a schema identifier, return an array of objects describing the tables of the schema.

Each returned JSON object in the array will have the form:
  {
    "oid": <int>,
    "name": <str>,
    "schema": <int>,
    "description": <str>,
    "owner_oid": <int>,
    "current_role_priv": [<str>],
    "current_role_owns": <bool>,
    "type": <str>
  }

The "type" field will be one of: "table", "view", or "materialized_view".

Args:
  sch_id: The OID or name of the schema.
*/
SELECT coalesce(jsonb_agg(table_data),'[]'::jsonb)
FROM msar.table_info_table() AS table_data
WHERE table_data.schema = sch_id;
$$;


--
-- Name: get_total_order(oid); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.get_total_order(tab_id oid) RETURNS jsonb
    LANGUAGE sql STABLE STRICT
    AS $$
WITH orderable_cte AS (
  SELECT DISTINCT attnum
  FROM pg_catalog.pg_attribute
    INNER JOIN pg_catalog.pg_type ON atttypid = pg_type.oid
    INNER JOIN pg_catalog.pg_opclass ON (
      pg_type.oid = opcintype
      OR pg_type.oid = opckeytype
      OR EXISTS (
        SELECT 1 FROM pg_catalog.pg_cast
        WHERE castsource = pg_type.oid
          AND casttarget = opcintype
          AND castmethod = 'b'
      )
    )
    INNER JOIN pg_catalog.pg_am ON opcmethod = pg_am.oid
  WHERE
    attrelid = tab_id
    AND attnum > 0
    AND NOT attisdropped
    AND amname = 'btree'
    AND opcdefault = true
    AND has_column_privilege(tab_id, attnum, 'SELECT')
  ORDER BY attnum
)
SELECT COALESCE(jsonb_agg(jsonb_build_object('attnum', attnum, 'direction', 'asc') ORDER BY attnum), '[]'::jsonb)
FROM orderable_cte
$$;


--
-- Name: get_type_options(regtype, integer, integer); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.get_type_options(typ_id regtype, typ_mod integer, typ_ndims integer) RETURNS jsonb
    LANGUAGE sql STRICT
    AS $$/*
Return the type options calculated from a type, typmod pair.

This function uses a number of hard-coded constants. The form of the returned object is determined
by the input type, but the keys will be a subset of:
  precision: the precision of a numeric or interval type. See PostgreSQL docs for details.
  scale: the scale of a numeric type
  fields: See PostgreSQL documentation of the `interval` type.
  length: Applies to "text" types where the user can specify the length.
  item_type: Gives the type of array members for array-types.
  original_type: The actual PostgreSQL type name for enum types.
  enum_values: An ordered list of valid enum labels for enum types.

Args:
  typ_id: an OID or valid type representing string will work here.
  typ_mod: The integer corresponding to the type options; see pg_attribute catalog table.
  typ_ndims: Used to determine whether the type is actually an array without an extra join.
*/
SELECT nullif(
  CASE
    WHEN typ_id = ANY('{numeric, _numeric}'::regtype[]) THEN
      jsonb_build_object(
        -- This calculation is modified from the relevant PostgreSQL source code. See the function
        -- numeric_typmod_precision(int32) at
        -- https://doxygen.postgresql.org/backend_2utils_2adt_2numeric_8c.html
        'precision', ((nullif(typ_mod, -1) - 4) >> 16) & 65535,
        -- This calculation is from numeric_typmod_scale(int32) at the same location
        'scale', (((nullif(typ_mod, -1) - 4) & 2047) # 1024) - 1024
      )
    WHEN typ_id = ANY('{interval, _interval}'::regtype[]) THEN
      jsonb_build_object(
        'precision', nullif(typ_mod & 65535, 65535),
        'fields', msar.get_interval_fields(typ_mod)
      )
    WHEN typ_id = ANY('{bpchar, _bpchar, varchar, _varchar}'::regtype[]) THEN
      -- For char and varchar types, the typemod is equal to 4 more than the set length.
      jsonb_build_object('length', nullif(typ_mod, -1) - 4)
    WHEN typ_id = ANY(
      '{bit, varbit, time, timetz, timestamp, timestamptz}'::regtype[]
      || '{_bit, _varbit, _time, _timetz, _timestamp, _timestamptz}'::regtype[]
    ) THEN
      -- For all these types, the typmod is equal to the precision.
      jsonb_build_object(
        'precision', nullif(typ_mod, -1)
      )
    WHEN (SELECT typtype FROM pg_catalog.pg_type WHERE oid = typ_id) = 'e' THEN
      jsonb_build_object(
        'original_type', typ_id::regtype::text,
        'enum_values',
        (SELECT jsonb_agg(enumlabel ORDER BY enumsortorder)
         FROM pg_catalog.pg_enum WHERE enumtypid = typ_id)
      )
    ELSE jsonb_build_object()
  END
  || CASE
    WHEN typ_ndims>0 THEN
      jsonb_build_object(
        'item_type',
        CASE
          WHEN (
            SELECT typtype
            FROM pg_catalog.pg_type
            WHERE oid = (SELECT typelem FROM pg_catalog.pg_type WHERE oid = typ_id)
          ) = 'e'
          THEN '_enum'
          -- This string wrangling is debatably dubious, but avoids a slow join.
          ELSE rtrim(typ_id::regtype::text, '[]')
        END
      )
    ELSE '{}'
  END,
  '{}'
)
$$;


--
-- Name: get_unique_local_identifier(text[], text); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.get_unique_local_identifier(existing_identifiers text[], base_identifier text) RETURNS text
    LANGUAGE plpgsql IMMUTABLE STRICT
    AS $$/*
  This function generates a unique identifier based on a given base identifier and list
  of existing identifiers.

  If the base identifier already exists in the provided array of existing identifiers,
  it appends a counter to ensure uniqueness, else it returns the base identifier.
*/
DECLARE
  unique_identifier text;
  counter integer := 0;
BEGIN
  unique_identifier := base_identifier;

  WHILE unique_identifier = ANY(existing_identifiers) LOOP
    counter := counter + 1;
    unique_identifier := format('%s %s', base_identifier, counter);
  END LOOP;

  RETURN unique_identifier;
END;
$$;


--
-- Name: grant_usage_on_custom_mathesar_types_to_public(); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.grant_usage_on_custom_mathesar_types_to_public() RETURNS void
    LANGUAGE plpgsql
    AS $_$
BEGIN
  EXECUTE string_agg(
    format(
      'GRANT USAGE ON TYPE %1$I.%2$I TO PUBLIC',
      pgn.nspname,
      pgt.typname
    ),
    E';\n'
  ) || E';\n'
  FROM pg_catalog.pg_type AS pgt
  JOIN pg_catalog.pg_namespace pgn ON pgn.oid = pgt.typnamespace
  WHERE (pgn.nspname = 'msar'
  OR pgn.nspname = '__msar'
  OR pgn.nspname = 'mathesar_types')
  AND (pgt.typtype = 'c' OR pgt.typtype = 'd')
  AND pgt.typcategory != 'A';
END;
$_$;


--
-- Name: has_dependents(oid, smallint); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.has_dependents(rel_id oid, att_id smallint) RETURNS boolean
    LANGUAGE sql STRICT
    AS $$/*
Return a boolean according to whether the column identified by the given oid, attnum pair is
referenced (i.e., would dropping that column require CASCADE?).

Args:
  rel_id: The relation of the attribute.
  att_id: The attnum of the attribute in the relation.
*/
SELECT EXISTS (
  SELECT 1 FROM pg_catalog.pg_depend WHERE refobjid=rel_id AND refobjsubid=att_id AND deptype='n'
);
$$;


--
-- Name: infer_column_data_type(regclass, smallint, numeric); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.infer_column_data_type(tab_id regclass, col_id smallint, test_perc numeric DEFAULT 100) RETURNS jsonb
    LANGUAGE plpgsql STRICT
    AS $_$/*
Infer the best type for a given column.

Note that we currently only try for `text` columns, since we only do this at import. I.e.,
if the column is some other type we just return that original type.

Args:
  tab_id: The OID of the table of the column whose type we're inferring.
  col_id: The attnum of the column whose type we're inferring.
*/
DECLARE
  inferred_type regtype;
  inferred_type_details jsonb;
  test_type_details msar.type_compat_details;
  infer_sequence_raw text[] := ARRAY[
    'boolean',
    'date',
    'numeric',
    'mathesar_types.mathesar_money',
    'uuid',
    'timestamp without time zone',
    'timestamp with time zone',
    'time without time zone',
    'interval',
    'mathesar_types.email',
    'mathesar_types.mathesar_json_array',
    'mathesar_types.mathesar_json_object',
    'mathesar_types.uri'
  ];
  infer_sequence regtype[];
  column_nonempty boolean;
  test_type regtype;
BEGIN
  infer_sequence := array_agg(pg_catalog.to_regtype(t))
    FILTER (WHERE pg_catalog.to_regtype(t) IS NOT NULL)
    FROM unnest(infer_sequence_raw) AS x(t);
  EXECUTE format(
    'SELECT EXISTS (SELECT 1 FROM %1$I.%2$I WHERE %3$I IS NOT NULL)',
    msar.get_relation_schema_name(tab_id),
    msar.get_relation_name(tab_id),
    msar.get_column_name(tab_id, col_id)
  ) INTO column_nonempty;
  inferred_type := atttypid FROM pg_catalog.pg_attribute WHERE attrelid=tab_id AND attnum=col_id;
  IF inferred_type <> 'text'::regtype OR NOT column_nonempty THEN
    RETURN jsonb_build_object('type', inferred_type);
  END IF;
  FOREACH test_type IN ARRAY infer_sequence
    LOOP
      test_type_details := msar.check_column_type_compat(tab_id, col_id, test_type, test_perc);
      IF test_type_details.type_compatible THEN
        inferred_type := test_type;
        inferred_type_details := to_jsonb(test_type_details) - 'type_compatible';
        EXIT;
      END IF;
    END LOOP;
  RETURN jsonb_strip_nulls(
    jsonb_build_object('type', inferred_type, 'details', inferred_type_details)
  );
END;
$_$;


--
-- Name: infer_table_column_data_types(regclass); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.infer_table_column_data_types(tab_id regclass) RETURNS jsonb
    LANGUAGE plpgsql STRICT
    AS $_$/*
Infer the best type for each column in the table.

Currently we only suggest different types for columns which originate as type `text`.

Args:
  tab_id: The OID of the table whose columns we're inferring types for.

The response JSON will have attnum keys, and values will be the result of `format_type`
for the inferred type of each column. Restricted to columns to which the user has access.

For tables with at most 9900 rows, we infer based on entire row set. For tables with more rows, we
decrease the percentage used to maintain an inference row set of 9,900-10,000 rows, down to a
minimum of 5% of the rows. This increases the performance of inference on large tables, at the cost
of possibly misidentifying the type of a column.

| row count  | perc |
|------------+------|
|   <= 9,900 | 100% |
|     19,900 |  50% |
|     39,900 |  25% |
|     99,900 |  10% |
| >= 199,900 |   5% |
*/
DECLARE
  test_perc integer;
BEGIN
EXECUTE(
  format(
    'SELECT GREATEST(LEAST(10000 / (COUNT(1) + 1), 100), 5) FROM %1$I.%2$I TABLESAMPLE SYSTEM(1)',
    msar.get_relation_schema_name(tab_id),
    msar.get_relation_name(tab_id)
  )
) INTO test_perc;
RETURN jsonb_object_agg(attnum, msar.infer_column_data_type(attrelid, attnum, test_perc))
FROM pg_catalog.pg_attribute
WHERE
  attrelid = tab_id
  AND attnum > 0
  AND NOT attisdropped
  AND has_column_privilege(attrelid, attnum, 'SELECT');
END;
$_$;


--
-- Name: insert_from_select(regclass, regclass, jsonb); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.insert_from_select(src_tab_id regclass, dst_tab_id regclass, mappings jsonb) RETURNS bigint
    LANGUAGE plpgsql STRICT
    AS $$/*
Insert records from a given source table to a destination/target table,
returning the number of records inserted.

Args:
  src_tab_id: The OID of the source table.(OID if temp table if inserting into existing table).
  dst_tab_id: The OID of the destination/target table.
  mappings: The column mappings b/w src and dst tables based on which data will be inserted.

mappings should have the following form:
[
  {"src_table_attnum": 1, "dst_table_attnum": 2},
  {"src_table_attnum": 3, "dst_table_attnum": 3},
  {...}
]
*/
DECLARE
  src_table_cols text;
  dst_table_cols text;
  insert_count bigint;
BEGIN
  SELECT
    string_agg(
      msar.build_cast_expr(
        quote_ident(src.attname),
        dst.atttypid::regclass::text,
        '{}'::jsonb
      ), ', '
    ),
    string_agg(quote_ident(dst.attname), ', ')
  INTO src_table_cols, dst_table_cols
  FROM jsonb_to_recordset(mappings) AS mapping(
    src_table_attnum smallint,
    dst_table_attnum smallint
  )
  LEFT JOIN pg_catalog.pg_attribute AS src ON
    src.attnum = mapping.src_table_attnum
    AND src.attrelid = src_tab_id
    AND NOT src.attisdropped
  LEFT JOIN pg_catalog.pg_attribute AS dst ON
    dst.attnum = mapping.dst_table_attnum
    AND dst.attrelid = dst_tab_id
    AND NOT dst.attisdropped;

  EXECUTE format(
    'INSERT INTO %I.%I(%s) SELECT %s FROM %I.%I',
    msar.get_relation_schema_name(dst_tab_id),
    msar.get_relation_name(dst_tab_id),
    dst_table_cols,
    src_table_cols,
    msar.get_relation_schema_name(src_tab_id),
    msar.get_relation_name(src_tab_id)
  );
  GET DIAGNOSTICS insert_count = ROW_COUNT;
  RETURN insert_count;
END;
$$;


--
-- Name: is_default_possibly_dynamic(oid, integer); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.is_default_possibly_dynamic(tab_id oid, col_id integer) RETURNS boolean
    LANGUAGE sql STRICT
    AS $$/*
Determine whether the default value for the given column is an expression or constant.

If the column default is an expression, then we return 'True', since that could be dynamic. If the
column default is a simple constant, we return 'False'. The check is not very sophisticated, and
errs on the side of returning 'True'. We simply pull apart the pg_node_tree representation of the
expression, and check whether the root node is a known function call type. Note that we do *not*
search any deeper in the tree than the root node. This means we won't notice that some expressions
are actually constant (or at least static), if they have a function call or operator as their root
node.

For example, the following would return 'True', even though they're not dynamic:
  3 + 5
  msar.cast_to_integer('8')

Args:
  tab_id: The OID of the table with the column.
  col_id: The attnum of the column in the table.
*/
SELECT
  -- This is a typical dynamic default like NOW() or CURRENT_DATE
  (split_part(substring(adbin, 2), ' ', 1) IN (('SQLVALUEFUNCTION'), ('FUNCEXPR')))
  OR
  -- This is an identity column `GENERATED {ALWAYS | DEFAULT} AS IDENTITY`
  (attidentity <> '')
  OR
  -- Other generated columns show up here.
  (attgenerated <> '')
FROM pg_catalog.pg_attribute LEFT JOIN pg_catalog.pg_attrdef ON attrelid=adrelid AND attnum=adnum
WHERE attrelid=tab_id AND attnum=col_id;
$$;


--
-- Name: is_mathesar_id_column(oid, integer); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.is_mathesar_id_column(tab_id oid, col_id integer) RETURNS boolean
    LANGUAGE sql STRICT
    AS $$/*
Determine whether the given column is our default Mathesar ID column.

The column in question is always attnum 1, and is created with the string

  id integer PRIMARY KEY GENERATED BY DEFAULT AS IDENTITY

Args:
  tab_id: The OID of the table whose column we'll check
  col_id: The attnum of the column in question
*/
SELECT col_id=1 AND attname='id' AND atttypid='integer'::regtype::oid AND attidentity <> ''
FROM pg_catalog.pg_attribute WHERE attrelid=tab_id AND attnum=col_id;
$$;


--
-- Name: is_pkey_col(oid, integer); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.is_pkey_col(rel_id oid, col_id integer) RETURNS boolean
    LANGUAGE sql STABLE STRICT
    AS $$/*
Return whether the given column is in the primary key of the given relation (e.g., table).

Args:
  rel_id:  The OID of the relation.
  col_id:  The attnum of the column in the relation.
*/
SELECT EXISTS (
  SELECT 1 FROM pg_catalog.pg_constraint WHERE
    ARRAY[col_id::smallint] <@ conkey AND conrelid=rel_id AND contype='p'
);
$$;


--
-- Name: list_by_record_summaries(oid, integer, integer, text, jsonb, jsonb); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.list_by_record_summaries(tab_id oid, limit_ integer, offset_ integer, search_ text DEFAULT NULL::text, table_record_summary_templates jsonb DEFAULT NULL::jsonb, linked_record_path jsonb DEFAULT NULL::jsonb) RETURNS jsonb
    LANGUAGE plpgsql STABLE
    AS $_$/*
Get record summaries from a table, optionally filtering by a search term. Results are sorted by
the summary text.

Args:
  tab_id: The OID of the table whose record summaries we'll get.
  limit_: The maximum number of record summaries to return.
  offset_: The number of record summaries to skip before returning results.
  search_: A search term to filter the summaries by. If provided, only summaries containing this
    term (case insensitive) in their text will be returned.
  table_record_summary_templates: (optional) A JSON object that maps table OIDs to record summary
    templates.
  linked_record_path: (optional) A JSON object that represents linkages via a simple many-to-many
    mapping to a record in another table. This can be used to determine whether the listed
    summaries are derived from records which are linked from the other table.

*/
DECLARE
  search_where_clause text := '';
  mapping_join_path jsonb;
  mapped_record_pkey text;
  final_sql text;
  result_json jsonb;
BEGIN
  IF search_ IS NOT NULL AND search_ <> '' THEN
    search_where_clause := format(' WHERE summary ILIKE %L', '%'||search_||'%');
  END IF;

  mapping_join_path := linked_record_path -> 'join_path';
  mapped_record_pkey := linked_record_path ->> 'record_pkey';

  final_sql := format(
    $q$
    WITH
      all_record_summaries AS ( %1$s ),
      filtered AS ( SELECT * FROM all_record_summaries %2$s ),
      sorted AS ( SELECT * FROM filtered ORDER BY summary LIMIT %3$s OFFSET %4$s ),
      results AS ( SELECT coalesce(jsonb_agg(sorted), '[]') AS results FROM sorted ),
      count_all_results AS ( SELECT count(*) AS num FROM filtered ),
      mapping_cte AS (%5$s),
      agg_mapping_cte AS (
        SELECT NULLIF(pg_catalog.jsonb_strip_nulls(pg_catalog.jsonb_build_object(
          'join_table', %6$L::bigint,
          'joined_values', pg_catalog.jsonb_object_agg(m.join_key, m.mapping_keys)
        )), '{}'::jsonb) AS mapping
        FROM mapping_cte m INNER JOIN sorted s ON m.join_key::text = s.key::text
      )
    SELECT
      jsonb_build_object(
        'count', count_all_results.num,
        'results', results.results,
        'mapping', agg_mapping_cte.mapping
      )
    FROM count_all_results, results, agg_mapping_cte
    $q$,
    /* 1 */ msar.build_record_summary_query_for_table(tab_id, NULL, table_record_summary_templates),
    /* 2 */ search_where_clause,
    /* 3 */ limit_,
    /* 4 */ offset_,
    /* 5 */ msar.get_simple_mapping_join_cte(mapping_join_path, mapped_record_pkey::text),
    /* 6 */ msar.get_simple_mapping_regclass(mapping_join_path)::oid
  );

  EXECUTE final_sql INTO result_json;
  RETURN result_json;
END;
$_$;


--
-- Name: list_column_privileges_for_current_role(regclass, smallint); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.list_column_privileges_for_current_role(tab_id regclass, attnum smallint) RETURNS jsonb
    LANGUAGE sql STABLE STRICT
    AS $$/*
Return a JSONB array of all privileges current_user holds on the passed table.
*/
SELECT coalesce(jsonb_agg(privilege), '[]'::jsonb)
FROM
  unnest(ARRAY['SELECT', 'INSERT', 'UPDATE', 'REFERENCES']) AS x(privilege),
  pg_catalog.has_column_privilege(tab_id, attnum, privilege) as has_privilege
WHERE has_privilege;
$$;


--
-- Name: list_database_privileges_for_current_role(oid); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.list_database_privileges_for_current_role(dat_id oid) RETURNS jsonb
    LANGUAGE sql STABLE STRICT
    AS $$/*
Return a JSONB array of all privileges current_user holds on the passed database.
*/
SELECT coalesce(jsonb_agg(privilege), '[]'::jsonb)
FROM
  unnest(
    ARRAY['CONNECT', 'CREATE', 'TEMPORARY']
  ) AS x(privilege),
  pg_catalog.has_database_privilege(dat_id, privilege) as has_privilege
WHERE has_privilege;
$$;


--
-- Name: list_db_priv(); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.list_db_priv() RETURNS jsonb
    LANGUAGE sql STABLE STRICT
    AS $$/*
Given a database name, returns a json array of objects with database privileges for non-inherited roles.

Each returned JSON object in the array has the form:
  {
    "role_oid": <int>,
    "direct" [<str>]
  }
*/
WITH priv_cte AS (
  SELECT
    jsonb_build_object(
      'role_oid', pgr.oid::bigint,
      'direct',  jsonb_agg(acl.privilege_type)
    ) AS p
  FROM
    pg_catalog.pg_roles AS pgr,
    pg_catalog.pg_database AS pgd,
    aclexplode(COALESCE(pgd.datacl, acldefault('d', pgd.datdba))) AS acl
  WHERE pgd.datname = pg_catalog.current_database()
    AND pgr.oid = acl.grantee AND pgr.rolname NOT LIKE 'pg_%'
  GROUP BY pgr.oid, pgd.oid
)
SELECT COALESCE(jsonb_agg(priv_cte.p), '[]'::jsonb) FROM priv_cte;
$$;


--
-- Name: list_records_from_table(oid, integer, integer, jsonb, jsonb, jsonb, jsonb, boolean, jsonb); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.list_records_from_table(tab_id oid, limit_ integer, offset_ integer, order_ jsonb, filter_ jsonb, group_ jsonb, joined_columns jsonb DEFAULT NULL::jsonb, return_record_summaries boolean DEFAULT false, table_record_summary_templates jsonb DEFAULT NULL::jsonb) RETURNS jsonb
    LANGUAGE plpgsql STABLE
    AS $_$/*
Get records from a table. Only columns to which the user has access are returned.

Args:
  tab_id: The OID of the table whose records we'll get
  limit_: The maximum number of rows we'll return
  offset_: The number of rows to skip before returning records from following rows.
  order_: An array of ordering definition objects.
  filter_: An array of filter definition objects.
  group_: An array of group definition objects.
  joined_columns: (optional) A jsonb list defining columns joined via a simple many-to-many linkage.
    See msar.get_joined_columns_expr_json for more details.
  return_record_summaries : Whether to return a summary for each record listed.
  table_record_summary_templates: (optional) A JSON object that maps table OIDs to record summary
    templates.

The order definition objects should have the form
  {"attnum": <int>, "direction": <text>}
*/
DECLARE
  expr_and_ctes jsonb;
  records jsonb;
BEGIN
  SELECT msar.build_record_list_query_components_with_ctes(
    tab_id,
    limit_,
    offset_,
    order_,
    filter_,
    group_,
    joined_columns
  ) INTO expr_and_ctes;

  EXECUTE format(
    $q$
    WITH
    count_cte AS ( %1$s ),
    enriched_results_cte AS ( %2$s ),
    results_ranked_cte AS (
      SELECT *, row_number() OVER (%3$s) - 1 AS __mathesar_result_idx FROM enriched_results_cte
    ),
    results_eq_cte AS (
      SELECT %11$s
    ),
    groups_cte AS ( SELECT %6$s ),
    summary_cte_self AS (%7$s)
    %8$s,
    summary_cte AS ( SELECT %10$s FROM enriched_results_cte %9$s )
    %12$s,
    joined_columns_summary_cte AS (%13$s),
    summaries_json_cte AS ( 
      SELECT
        jsonb_build_object(
          'linked_record_summaries',
          NULLIF(
            to_jsonb(summary_cte) - 'summary_self' - 'count_hack',
            '{}'::jsonb
          ),
          'record_summaries',
          NULLIF(
            to_jsonb(summary_cte) - 'count_hack' -> 'summary_self',
            '{}'::jsonb
          ),
          'joined_record_summaries', NULLIF(
            to_jsonb(joined_columns_summary_cte) - 'count_hack',
            '{}'::jsonb
          )
        )
      AS sj
      FROM summary_cte, joined_columns_summary_cte
    ),
    records_json_cte AS ( SELECT jsonb_build_object(
      'results', %4$s,
      'count', coalesce(max(count_cte.count), 0),
      'grouping', %5$s
    ) AS rj
    FROM enriched_results_cte
      LEFT JOIN groups_cte ON enriched_results_cte.__mathesar_gid = groups_cte.id
      CROSS JOIN count_cte
    )
    SELECT records_json_cte.rj || summaries_json_cte.sj
    FROM records_json_cte, summaries_json_cte;
    $q$,
    /* %1 */ expr_and_ctes ->> 'count_cte_query',
    /* %2 */ expr_and_ctes ->> 'results_cte_query',
    /* %3 */ expr_and_ctes ->> 'order_by_expr',
    /* %4 */ COALESCE(
      msar.build_results_jsonb_array_expr(
        'enriched_results_cte',
        expr_and_ctes ->> 'order_by_expr'
      ),
      'NULL'
    ),
    /* %5 */ COALESCE(
      msar.build_grouping_results_jsonb_expr(tab_id, 'groups_cte', group_),
      'NULL'
    ),
    /* %6 */ COALESCE(
      msar.build_groups_cte_expr(tab_id, 'results_eq_cte', 'results_ranked_cte', group_),
      'NULL AS id'
    ),
    /* %7 */ msar.build_record_summary_query_for_table(
      tab_id,
      null,
      table_record_summary_templates
    ),
    /* %8 */ msar.build_linked_record_summaries_ctes(
      tab_id,
      table_record_summary_templates
    ),
    /* %9 */ msar.build_summary_join_expr_for_table(tab_id, 'enriched_results_cte'),
    /* %10 */ COALESCE(
      NULLIF(
        concat_ws(', ',
          msar.build_summary_json_expr_for_table(tab_id),
          CASE WHEN return_record_summaries
          THEN msar.build_self_summary_json_expr(tab_id)
          END
        ), ''
      ), 'COUNT(1) AS count_hack'
      -- count_hack ensures that summary_cte is not empty,
      -- which in turn helps to generate summaries_json_cte
    ),
    /* %11 */ msar.build_results_eq_cte_expr(tab_id, 'results_ranked_cte', group_),
    /* %12 */ msar.build_joined_columns_summaries_ctes(
      'enriched_results_cte',
      joined_columns,
      table_record_summary_templates
    ),
    /* %13 */ COALESCE(
      NULLIF(msar.build_joined_columns_summaries_expr(joined_columns), ''),
      'SELECT COUNT(1) AS count_hack'
    )
  ) INTO records;
  RETURN records;
END;
$_$;


--
-- Name: list_roles(); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.list_roles() RETURNS jsonb
    LANGUAGE sql STABLE
    AS $$/*
Return a json array of objects with the list of roles in a database server,
excluding pg system roles.

Each returned JSON object in the array has the form:
  {
    "oid": <int>
    "name": <str>
    "super": <bool>
    "inherits": <bool>
    "create_role": <bool>
    "create_db": <bool>
    "login": <bool>
    "description": <str|null>
    "members": <[
        { "oid": <int>, "admin": <bool> }
      ]|null>
  }
*/
SELECT jsonb_agg(role_data)
FROM msar.role_info_table() AS role_data
WHERE role_data.name NOT LIKE 'pg_%';
$$;


--
-- Name: list_schema_privileges(regnamespace); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.list_schema_privileges(sch_id regnamespace) RETURNS jsonb
    LANGUAGE sql STABLE STRICT
    AS $$/*
Given a schema, returns a json array of objects with direct, non-default schema privileges

Each returned JSON object in the array has the form:
  {
    "role_oid": <int>,
    "direct" [<str>]
  }
*/
WITH priv_cte AS (
  SELECT
    jsonb_build_object(
      'role_oid', pgr.oid::bigint,
      'direct',  jsonb_agg(acl.privilege_type)
    ) AS p
  FROM
    pg_catalog.pg_roles AS pgr,
    pg_catalog.pg_namespace AS pgn,
    aclexplode(COALESCE(pgn.nspacl, acldefault('n', pgn.nspowner))) AS acl
  WHERE pgn.oid = sch_id AND pgr.oid = acl.grantee AND pgr.rolname NOT LIKE 'pg_%'
  GROUP BY pgr.oid, pgn.oid
)
SELECT COALESCE(jsonb_agg(priv_cte.p), '[]'::jsonb) FROM priv_cte;
$$;


--
-- Name: list_schema_privileges_for_current_role(regnamespace); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.list_schema_privileges_for_current_role(sch_id regnamespace) RETURNS jsonb
    LANGUAGE sql STABLE STRICT
    AS $$/*
Return a JSONB array of all privileges current_user holds on the passed schema.
*/
SELECT coalesce(jsonb_agg(privilege), '[]'::jsonb)
FROM
  unnest(
    ARRAY['USAGE', 'CREATE']
  ) AS x(privilege),
  pg_catalog.has_schema_privilege(sch_id, privilege) as has_privilege
WHERE has_privilege;
$$;


--
-- Name: list_schemas(); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.list_schemas() RETURNS jsonb
    LANGUAGE sql STABLE
    AS $$/*
Return a json array of objects describing the user-defined schemas in the database.

PostgreSQL system schemas are ignored.

Internal Mathesar-specifc schemas are INCLUDED. These should be filtered out by the caller. This
behavior is to avoid tight coupling between this function and other SQL files that might need to
define additional Mathesar-specific schemas as our codebase grows.

Each returned JSON object in the array will have the form:
  {
    "oid": <int>
    "name": <str>
    "description": <str|null>
    "owner_oid": <int>,
    "current_role_priv": [<str>],
    "current_role_owns": <bool>,
    "table_count": <int>
  }
*/
SELECT jsonb_agg(schema_data)
FROM msar.schema_info_table() AS schema_data
WHERE schema_data.name <> 'information_schema'
AND schema_data.name NOT LIKE 'pg_%';
$$;


--
-- Name: list_table_privileges(regclass); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.list_table_privileges(tab_id regclass) RETURNS jsonb
    LANGUAGE sql STABLE STRICT
    AS $$/*
Given a table, returns a json array of objects with direct, non-default table privileges.

Each returned JSON object in the array has the form:
  {
    "role_oid": <int>,
    "direct" [<str>]
  }
*/
WITH priv_cte AS (
  SELECT
    jsonb_build_object(
      'role_oid', pgr.oid::bigint,
      'direct',  jsonb_agg(acl.privilege_type)
    ) AS p
  FROM
    pg_catalog.pg_roles AS pgr,
    pg_catalog.pg_class AS pgc,
    aclexplode(COALESCE(pgc.relacl, acldefault('r', pgc.relowner))) AS acl
  WHERE pgc.oid = tab_id AND pgr.oid = acl.grantee AND pgr.rolname NOT LIKE 'pg_%'
  GROUP BY pgr.oid, pgc.oid
)
SELECT COALESCE(jsonb_agg(priv_cte.p), '[]'::jsonb) FROM priv_cte;
$$;


--
-- Name: list_table_privileges_for_current_role(regclass); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.list_table_privileges_for_current_role(tab_id regclass) RETURNS jsonb
    LANGUAGE sql STABLE STRICT
    AS $$/*
Return a JSONB array of all privileges current_user holds on the passed table.
*/
SELECT coalesce(jsonb_agg(privilege), '[]'::jsonb)
FROM
  unnest(
    ARRAY['SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE', 'REFERENCES', 'TRIGGER']
  ) AS x(privilege),
  pg_catalog.has_table_privilege(tab_id, privilege) as has_privilege
WHERE has_privilege;
$$;


--
-- Name: mathesar_system_schemas(); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.mathesar_system_schemas() RETURNS text[]
    LANGUAGE sql STABLE
    AS $$/*
Return a text array of the Mathesar System schemas.

Update this function whenever the list changes.
*/
SELECT ARRAY['msar', '__msar', 'mathesar_types']
$$;


--
-- Name: month_to_degrees(date); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.month_to_degrees(date_ date) RETURNS double precision
    LANGUAGE sql
    AS $$/*
Convert discrete month to degrees.

To get the fraction of 12 months passed, we extract the month from date, subtract 1 from 
the result to confine it in range [0, 12) and divide by 12, then to get the equivalent
fraction of 360°, we multiply by 360, which is equivalent to multiply by 30.

Examples:
  2023-05-02 => 120
  2023-07-12 => 180
  2023-01-01 =>   0
  2023-12-12 => 330
*/
SELECT ((EXTRACT(MONTH FROM date_) - 1)::double precision) * 30;    
$$;


--
-- Name: move_columns_to_referenced_table(regclass, regclass, smallint[]); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.move_columns_to_referenced_table(source_tab_id regclass, target_tab_id regclass, move_col_ids smallint[]) RETURNS void
    LANGUAGE plpgsql
    AS $_$
DECLARE
  source_join_col_id smallint;
  target_join_col_id smallint;
  preexisting_col_expr CONSTANT text := msar.build_all_columns_expr(target_tab_id);
  move_col_expr CONSTANT text := msar.build_columns_expr(source_tab_id, move_col_ids);
  move_col_defs CONSTANT jsonb := msar.get_extracted_col_def_jsonb(source_tab_id, move_col_ids);
  move_con_defs CONSTANT jsonb := msar.get_extracted_con_def_jsonb(source_tab_id, move_col_ids);
  added_col_ids smallint[];
BEGIN
  -- TODO Add a custom validator that throws pretty errors in these scenario:
    -- test to make sure no multi-col fkeys reference the moved columns
    -- just throw error if _any_ multicol constraint references the moved columns.
    -- check behavior if one of the moving columns is referenced by another table (should raise)
  SELECT conkey, confkey INTO source_join_col_id, target_join_col_id
    FROM msar.get_fkey_map_table(source_tab_id)
    WHERE target_oid = target_tab_id;
  IF move_col_ids @> ARRAY[source_join_col_id] THEN
    RAISE EXCEPTION 'The joining column cannot be moved.';
  END IF;
  added_col_ids := msar.add_columns(target_tab_id, move_col_defs, true);
  EXECUTE format(
    $q$WITH merged_cte AS (
      SELECT DISTINCT %1$s, %2$s
      FROM %3$I.%4$I JOIN %6$I.%7$I ON %3$I.%4$I.%5$I = %6$I.%7$I.%8$I
    ), row_numbered_cte AS (
      SELECT *, row_number() OVER (PARTITION BY %8$I ORDER BY %9$s) AS __msar_row_number
      FROM merged_cte
    ), update_target_cte AS (
      UPDATE %6$I.%7$I SET (%9$s) = (
        SELECT %9$s
        FROM row_numbered_cte
        WHERE row_numbered_cte.%8$I=%6$I.%7$I.%8$I
        AND __msar_row_number = 1
      )
      RETURNING *
    ), insert_cte AS (
      INSERT INTO %6$I.%7$I (%10$s)
      SELECT %10$s FROM row_numbered_cte
      WHERE __msar_row_number <> 1
      RETURNING *
    )
    UPDATE %3$I.%4$I SET %5$I = insert_cte.%8$I
    FROM update_target_cte JOIN insert_cte %11$s
    WHERE %3$I.%4$I.%5$I = update_target_cte.%8$I AND %12$s
    $q$,
    preexisting_col_expr,
    move_col_expr,
    msar.get_relation_schema_name(source_tab_id),
    msar.get_relation_name(source_tab_id),
    msar.get_column_name(source_tab_id, source_join_col_id),
    msar.get_relation_schema_name(target_tab_id),
    msar.get_relation_name(target_tab_id),
    msar.get_column_name(target_tab_id, target_join_col_id),
    msar.build_unqualified_columns_expr(source_tab_id, move_col_ids),
    msar.build_unqualified_columns_expr(
      target_tab_id, msar.get_other_column_ids(target_tab_id, ARRAY[target_join_col_id])
    ),
    msar.build_source_update_cte_join_condition_expr(
      target_tab_id, target_join_col_id, added_col_ids, 'update_target_cte', 'insert_cte'
    ),
    msar.build_source_update_move_cols_equal_expr(source_tab_id, move_col_ids, 'insert_cte')
  );
  PERFORM msar.add_constraints(target_tab_id, move_con_defs);
  PERFORM msar.drop_columns(source_tab_id, variadic move_col_ids);
END;
$_$;


--
-- Name: obj_description(oid, text); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.obj_description(obj_id oid, catalog_name text) RETURNS text
    LANGUAGE plpgsql
    AS $$/*
Transparent wrapper for obj_description. Putting it in the `msar` namespace helps route all DB calls
from Python through a single Python module.
*/
  BEGIN
    RETURN obj_description(obj_id, catalog_name);
  END
$$;


--
-- Name: patch_record_in_table(oid, anycompatible, jsonb, boolean, jsonb); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.patch_record_in_table(tab_id oid, rec_id anycompatible, rec_def jsonb, return_record_summaries boolean DEFAULT false, table_record_summary_templates jsonb DEFAULT NULL::jsonb) RETURNS jsonb
    LANGUAGE plpgsql
    AS $_$/*
Modify (update/patch) a record in a table.

Args:
  tab_id: The OID of the table whose record we'll delete.
  rec_id: The primary key value of the record we'll modify.
  rec_patch: A JSON object defining the parts of the record to patch.

Only tables with a single primary key column are supported.

The `rec_def` object's form is defined by the record being updated.  It should have keys
corresponding to the attnums of desired columns and values corresponding to values we should set.
*/
DECLARE
  rec_modified jsonb;
  num_updated bigint;
BEGIN
  EXECUTE format(
    $p$ %1$s %2$s $p$,
    msar.build_update_expr(tab_id, rec_def),
    msar.build_where_clause(
      tab_id, jsonb_build_object(
        'type', 'equal', 'args', jsonb_build_array(
          jsonb_build_object('type', 'literal', 'value', rec_id),
          jsonb_build_object('type', 'attnum', 'value', msar.get_pk_column(tab_id))
        )
      )
    )
  );
  GET DIAGNOSTICS num_updated = ROW_COUNT;
  IF num_updated = 0 THEN
    RAISE EXCEPTION 'No rows updated';
  END IF;
  rec_modified := msar.get_record_from_table(
    tab_id,
    rec_id,
    null,
    return_record_summaries,
    table_record_summary_templates
  );
  RETURN jsonb_build_object(
    'results', rec_modified -> 'results',
    'record_summaries', rec_modified -> 'record_summaries',
    'linked_record_summaries', rec_modified -> 'linked_record_summaries'
  );
END;
$_$;


--
-- Name: patch_schema(oid, jsonb); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.patch_schema(sch_id oid, patch jsonb) RETURNS jsonb
    LANGUAGE plpgsql STRICT
    AS $$/*
Modify a schema according to the given patch.

Args:
  sch_id: The OID of the schema.
  patch: A JSONB object with the following keys:
    - name: (optional) The new name of the schema
    - description: (optional) The new description of the schema. To remove a description, pass an
      empty string or NULL.

Returns:
  A json object describing the user-defined schema in the database.
*/
BEGIN
  PERFORM msar.rename_schema(sch_id, patch->>'name');
  PERFORM CASE WHEN patch ? 'description'
  THEN msar.set_schema_description(sch_id, patch->>'description') END;
  RETURN msar.get_schema(sch_id);
END;
$$;


--
-- Name: point_to_month(point); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.point_to_month(point_ point) RETURNS integer
    LANGUAGE sql
    AS $$/*
Convert a point to degrees and then to discrete month.

Point is converted to month by:
- first converting to degrees by calculating the inverse tangent of the point.
- then converting the degrees to the discrete month.
- If the point is on or very near to the origin, we return null.

Args:
  point_: A point that represents a vector.

Returns:
  discrete month corresponding to the vector represented by point_.
*/
SELECT CASE
  /*
  When both sine and cosine are zero, the answer should be null.

  To avoid garbage output caused by the precision errors of the float
  variables, it's better to extend the condition to:
  Output is null when the distance of the point from the origin is less than
  a certain epsilon. (Epsilon here is 1e-10)
  */
  WHEN point_ <-> point(0,0) < 1e-10 THEN NULL
  ELSE msar.degrees_to_month(atan2d(point_[0],point_[1]))
END;
$$;


--
-- Name: point_to_time(point); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.point_to_time(point_ point) RETURNS time without time zone
    LANGUAGE sql
    AS $$/*
Convert a point to degrees and then to time.

Point is converted to time by:
- first converting to degrees by calculating the inverse tangent of the point
- then converting the degrees to the time.
- If the point is on or very near the origin, we return null.

Args:
  point_: A point that represents a vector

Returns:
  time corresponding to the vector represented by point_.
*/
SELECT CASE
  /*
  When both sine and cosine are zero, the answer should be null.

  To avoid garbage output caused by the precision errors of the float
  variables, it's better to extend the condition to:
  Output is null when the distance of the point from the origin is less than
  a certain epsilon. (Epsilon here is 1e-10)
  */
  WHEN point_ <-> point(0,0) < 1e-10 THEN NULL
  ELSE msar.degrees_to_time(atan2d(point_[0],point_[1]))
END;
$$;


--
-- Name: prepare_table_for_import(oid, text, text[], text); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.prepare_table_for_import(sch_id oid, tab_name text, col_names text[], comment_ text) RETURNS jsonb
    LANGUAGE plpgsql
    AS $$/*
Add a table, with a default id column, returning a JSON object containing a properly formatted SQL
statement to carry out `COPY FROM`, table_oid & table_name of the created table.

Each returned JSON object will have the form:
  {
    "copy_sql": <str>,
    "table_oid": <int>,
    "table_name": <str>,
    "renamed_columns": <arr>
  }

Args:
  sch_id: The OID of the schema where the table will be created.
  tab_name (optional): The unquoted name for the new table.
  col_defs: The columns for the new table, in order.
  comment_ (optional): The comment for the new table.
*/
DECLARE
  sch_name text;
  rel_name text;
  col_defs jsonb;
  mathesar_table json;
  rel_id oid;
  col_names_sql text;
  copy_sql text;
BEGIN
  -- Build column definition jsonb
  col_defs := jsonb_agg(jsonb_build_object('name', n)) FROM unnest(col_names) AS x(n);
  -- Create string table
  mathesar_table := msar.add_mathesar_table(sch_id, tab_name, NULL, col_defs, NULL, NULL, comment_);
  rel_id := mathesar_table ->> 'oid';
  -- Get unquoted schema and table name for the created table
  SELECT nspname, relname INTO sch_name, rel_name
  FROM pg_catalog.pg_class AS pgc
  LEFT JOIN pg_catalog.pg_namespace AS pgn
  ON pgc.relnamespace = pgn.oid
  WHERE pgc.oid = rel_id;
  -- Aggregate TEXT type column names of the created table
  SELECT string_agg(quote_ident(attname), ', ') INTO col_names_sql
  FROM pg_catalog.pg_attribute
  WHERE attrelid = rel_id AND atttypid = 'TEXT'::regtype::oid;
  -- Create a properly formatted COPY SQL string
  copy_sql := format('COPY %I.%I (%s) FROM STDIN', sch_name, rel_name, col_names_sql);
  RETURN jsonb_build_object(
    'copy_sql', copy_sql,
    'table_oid', rel_id::bigint,
    'table_name', relname,
    'renamed_columns', mathesar_table -> 'renamed_columns',
    'pkey_column_attnum', mathesar_table -> 'pkey_column_attnum'
  ) FROM pg_catalog.pg_class WHERE oid = rel_id;
END;
$$;


--
-- Name: prepare_temp_table_for_import(text, text[]); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.prepare_temp_table_for_import(tab_name text, col_names text[]) RETURNS jsonb
    LANGUAGE plpgsql
    AS $$/*
Add a temp table, returning a JSON object containing a properly formatted SQL
statement to carry out `COPY FROM`, table_oid of the created temp table.

Each returned JSON object will have the form:
  {
    "copy_sql": <str>,
    "table_oid": <int>
  }

Args:
  tab_name (optional): The unquoted name for the new temp table.
  col_defs: The columns for the new table, in order.
*/
DECLARE
  col_defs jsonb;
  column_defs __msar.col_def[];
  prefix text;
  table_count integer := 1;
  uq_tab_name text;
  sch_name text;
  rel_id bigint;
  col_names_sql text;
  copy_sql text;
BEGIN
  -- Passing 'id' as column name gets filtered out by __msar.process_col_def_jsonb,
  -- pass NULL when 'id' is encountered.
  col_defs := jsonb_agg(jsonb_build_object('name', NULLIF(n, 'id'))) FROM unnest(col_names) AS x(n);

  IF NULLIF(tab_name, '') IS NOT NULL AND NOT EXISTS(
      SELECT oid FROM pg_catalog.pg_class WHERE relname = tab_name AND relpersistence = 't'
    )
  THEN
    uq_tab_name := tab_name;
  ELSE
    prefix := 'Temp Table ';
    uq_tab_name := prefix || table_count;
    -- avoid name collisions
    WHILE EXISTS (
      SELECT oid FROM pg_catalog.pg_class WHERE relname = uq_tab_name AND relpersistence = 't'
    ) LOOP
      table_count := table_count + 1;
      uq_tab_name := prefix || table_count;
    END LOOP;
  END IF;
  column_defs := __msar.process_col_def_jsonb(0, col_defs, false);
  PERFORM msar.add_temp_table(uq_tab_name, column_defs);

  SELECT nspname, pgc.oid INTO sch_name, rel_id
  FROM pg_catalog.pg_class AS pgc
  LEFT JOIN pg_catalog.pg_namespace AS pgn
  ON pgc.relnamespace = pgn.oid
  WHERE pgc.relname = uq_tab_name AND pgc.relpersistence = 't';

  -- Aggregate TEXT type column names of the created table
  SELECT string_agg(quote_ident(attname), ', ') INTO col_names_sql
  FROM pg_catalog.pg_attribute
  WHERE attrelid = rel_id AND atttypid = 'TEXT'::regtype::oid;

  -- Create a properly formatted COPY SQL string
  copy_sql := format('COPY %I.%I(%s) FROM STDIN', sch_name, uq_tab_name, col_names_sql);

  RETURN jsonb_build_object(
    'copy_sql', copy_sql,
    'table_oid', rel_id::bigint
  ) FROM pg_catalog.pg_class WHERE oid = rel_id;
END;
$$;


--
-- Name: raise_exception(text); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.raise_exception(err_msg text) RETURNS void
    LANGUAGE plpgsql STRICT
    AS $$/*
Utility function to raise an exceptions with an error message.

Having this utility function allows us to raise exceptions within SQL functions
where raising exceptions isn't otherwise possible.
*/
BEGIN
  RAISE EXCEPTION '%', err_msg;
END;
$$;


--
-- Name: rename_column(oid, integer, text); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.rename_column(tab_id oid, col_id integer, new_col_name text) RETURNS smallint
    LANGUAGE plpgsql STRICT
    AS $$/*
Change a column name, returning the command executed

Args:
  tab_id: The OID of the table whose column we're renaming
  col_id: The ID of the column to rename
  new_col_name: The unquoted new name for the column.
*/
DECLARE
  old_col_name text;
BEGIN
  old_col_name := msar.get_column_name(tab_id, col_id);
  IF old_col_name <> new_col_name THEN
    EXECUTE format(
      'ALTER TABLE %I.%I RENAME COLUMN %I TO %I',
      msar.get_relation_schema_name(tab_id),
      msar.get_relation_name(tab_id),
      old_col_name,
      new_col_name
    );
    RETURN col_id;
  ELSE
    RETURN null;
  END IF;
END;
$$;


--
-- Name: rename_schema(oid, text); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.rename_schema(sch_id oid, new_sch_name text) RETURNS void
    LANGUAGE plpgsql STRICT
    AS $$/*
Change a schema's name

Args:
  sch_id: The OID of the schema to rename
  new_sch_name: A new for the schema, UNQUOTED
*/
DECLARE
  old_sch_name text := msar.get_schema_name(sch_id);
BEGIN
  IF old_sch_name = new_sch_name THEN
    -- Return early if the names are the same. This avoids an error from Postgres.
    RETURN;
  END IF;
  EXECUTE format('ALTER SCHEMA %I RENAME TO %I', old_sch_name, new_sch_name);
END;
$$;


--
-- Name: rename_table(oid, text); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.rename_table(tab_id oid, new_tab_name text) RETURNS void
    LANGUAGE plpgsql STRICT
    AS $$/*
Change a table's name.

Args:
  tab_id: the OID of the table whose name we want to change
  new_tab_name: unquoted, unqualified table name
*/
DECLARE
  old_tab_name text := msar.get_relation_name(tab_id);
BEGIN
  IF old_tab_name <> new_tab_name THEN
    EXECUTE format(
      'ALTER TABLE %I.%I RENAME TO %I',
      msar.get_relation_schema_name(tab_id),
      old_tab_name,
      new_tab_name
    );
  END IF;
END;
$$;


--
-- Name: replace_database_privileges_for_roles(jsonb); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.replace_database_privileges_for_roles(priv_spec jsonb) RETURNS jsonb
    LANGUAGE plpgsql STRICT
    AS $$/*
Grant/Revoke privileges for a set of roles on the current database.

Args:
  priv_spec: An array defining the privileges to grant or revoke for each role.

Each object in the priv_spec should have the form:
{role_oid: <int>, privileges: SET<"CONNECT"|"CREATE"|"TEMPORARY">}

Any privilege that exists in the privileges subarray will be granted. Any which is missing will be
revoked.
*/
BEGIN
EXECUTE string_agg(
  msar.build_database_privilege_replace_expr(role_oid, direct),
  E';\n'
) || ';'
FROM jsonb_to_recordset(priv_spec) AS x(role_oid regrole, direct jsonb);
RETURN msar.list_db_priv();
END;
$$;


--
-- Name: replace_schema_privileges_for_roles(regnamespace, jsonb); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.replace_schema_privileges_for_roles(sch_id regnamespace, priv_spec jsonb) RETURNS jsonb
    LANGUAGE plpgsql STRICT
    AS $$/*
Grant/Revoke privileges for a set of roles on the given schema.

Args:
  sch_id The OID of the schema for which we're setting privileges for roles.
  priv_spec: An array defining the privileges to grant or revoke for each role.

Each object in the priv_spec should have the form:
{role_oid: <int>, privileges: SET<"USAGE"|"CREATE">}

Any privilege that exists in the privileges subarray will be granted. Any which is missing will be
revoked.
*/
BEGIN
EXECUTE string_agg(
  msar.build_schema_privilege_replace_expr(sch_id, role_oid, direct),
  E';\n'
) || ';'
FROM jsonb_to_recordset(priv_spec) AS x(role_oid regrole, direct jsonb);
RETURN msar.list_schema_privileges(sch_id);
END;
$$;


--
-- Name: replace_table_privileges_for_roles(regclass, jsonb); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.replace_table_privileges_for_roles(tab_id regclass, priv_spec jsonb) RETURNS jsonb
    LANGUAGE plpgsql STRICT
    AS $$/*
Grant/Revoke privileges for a set of roles on the given table.

Args:
  tab_id The OID of the table for which we're setting privileges for roles.
  priv_spec: An array defining the privileges to grant or revoke for each role.

Each object in the priv_spec should have the form:
{role_oid: <int>, privileges: SET<"INSERT"|"SELECT"|"UPDATE"|"DELETE"|"TRUNCATE"|"REFERENCES"|"TRIGGER">}

Any privilege that exists in the privileges subarray will be granted. Any which is missing will be
revoked.
*/
BEGIN
EXECUTE string_agg(
  msar.build_table_privilege_replace_expr(tab_id, role_oid, direct),
  E';\n'
) || ';'
FROM jsonb_to_recordset(priv_spec) AS x(role_oid regrole, direct jsonb);
RETURN msar.list_table_privileges(tab_id);
END;
$$;


--
-- Name: reset_mash(regclass, smallint, jsonb); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.reset_mash(tab_id regclass, col_id smallint, uri_mash_map jsonb) RETURNS void
    LANGUAGE plpgsql STRICT
    AS $_$/*
Resets the outdated "mash" for a given json/jsonb file column.

A typical file column has records in the following form:
{
  "uri": "s3://mathesar-storages/admin/20250915-181554471621/mathesar_logo.jpeg",
  "mash": "b0a15ad5117a008daf8671e0d3bc552161889f4beac01fb1ff10807f36fade69"
}

uri_mash_map should have the following form:
{
  <uri_1> : <mash_1>,
  <uri_2> : <mash_2>
  ...
}

Args:
  tab_id: The OID of the target table.
  col_id: The attnum of a file json(b) column.
  uri_mash_map: A map of uri and the new mash.
*/
DECLARE
  sch_name text := msar.get_relation_schema_name(tab_id);
  tab_name text := msar.get_relation_name(tab_id);
  col_name text := msar.get_column_name(tab_id, col_id);
BEGIN
  EXECUTE format(
    $j$
      UPDATE %1$I.%2$I
      SET %3$I = jsonb_set(%2$I.%3$I::jsonb, '{mash}', mapping.mash)
      FROM jsonb_each(%4$L) AS mapping(uri, mash)
      WHERE %2$I.%3$I ->> 'uri' = mapping.uri
    $j$,
    sch_name,
    tab_name,
    col_name,
    uri_mash_map
  );
END;
$_$;


--
-- Name: retype_column(regclass, smallint, text, jsonb); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.retype_column(tab_id regclass, col_id smallint, new_type text, cast_options jsonb) RETURNS text
    LANGUAGE plpgsql STRICT
    AS $$/*
Alter a column's type, returning the text of the expression executed.

Args:
  tab_id: The OID of the table containing the column whose type we'll alter.
  col_id: The attnum of the column whose type we'll alter.
  new_type: The target type to which we'll alter the column.
  cast_options: Suggestions to be used while type casting.
*/
DECLARE
  retype_col_sql text;
BEGIN
  SELECT format(
    'ALTER TABLE %I.%I ALTER COLUMN %I TYPE %s USING %s',
    msar.get_relation_schema_name(tab_id),
    msar.get_relation_name(tab_id),
    msar.get_column_name(tab_id, col_id),
    new_type,
    msar.build_cast_expr(quote_ident(msar.get_column_name(tab_id, col_id)), new_type, cast_options)
  ) INTO retype_col_sql;
  EXECUTE retype_col_sql;
  RETURN retype_col_sql;
END;
$$;


--
-- Name: role_info_table(); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.role_info_table() RETURNS TABLE(oid bigint, name name, super boolean, inherits boolean, create_role boolean, create_db boolean, login boolean, description text, members jsonb)
    LANGUAGE sql STABLE
    AS $$/*
Returns a table describing all the roles present on the database server.
*/
WITH rolemembers as (
  SELECT
    pgr.oid AS oid,
    jsonb_agg(
      jsonb_build_object(
        'oid', pgm.member::bigint,
        'admin', pgm.admin_option
      )
    ) AS members
    FROM pg_catalog.pg_roles pgr
      INNER JOIN pg_catalog.pg_auth_members pgm ON pgr.oid=pgm.roleid
    GROUP BY pgr.oid
)
SELECT
  r.oid::bigint AS oid,
  r.rolname AS name,
  r.rolsuper AS super,
  r.rolinherit AS inherits,
  r.rolcreaterole AS create_role,
  r.rolcreatedb AS create_db,
  r.rolcanlogin AS login,
  pg_catalog.shobj_description(r.oid, 'pg_authid') AS description,
  rolemembers.members AS members
FROM pg_catalog.pg_roles r
LEFT OUTER JOIN rolemembers ON r.oid = rolemembers.oid;
$$;


--
-- Name: sanitize_direction(text); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.sanitize_direction(direction text) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
    AS $$/*
*/
SELECT CASE lower(direction)
  WHEN 'asc' THEN 'ASC'
  WHEN 'desc' THEN 'DESC'
END;
$$;


--
-- Name: schema_info_table(); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.schema_info_table() RETURNS TABLE(oid bigint, name name, description text, owner_oid bigint, current_role_priv jsonb, current_role_owns boolean, table_count integer)
    LANGUAGE sql STABLE
    AS $$
SELECT
  s.oid::bigint AS oid,
  s.nspname AS name,
  pg_catalog.obj_description(s.oid) AS description,
  s.nspowner::bigint AS owner_oid,
  msar.list_schema_privileges_for_current_role(s.oid) AS current_role_priv,
  pg_catalog.pg_has_role(s.nspowner, 'USAGE') AS current_role_owns,
  COALESCE(count(c.oid), 0) AS table_count
FROM pg_catalog.pg_namespace s
LEFT JOIN pg_catalog.pg_class c ON c.relnamespace = s.oid AND c.relkind = 'r'
GROUP BY
  s.oid,
  s.nspname,
  s.nspowner
ORDER BY s.nspname;
-- Filter on relkind so that we only count tables. This must be done in the ON clause so that
-- we still get a row for schemas with no tables.
$$;


--
-- Name: search_records_from_table(oid, jsonb, integer, integer, boolean, jsonb); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.search_records_from_table(tab_id oid, search_ jsonb, limit_ integer, offset_ integer DEFAULT 0, return_record_summaries boolean DEFAULT false, table_record_summary_templates jsonb DEFAULT NULL::jsonb) RETURNS jsonb
    LANGUAGE plpgsql
    AS $_$/*
Get records from a table, filtering and sorting according to a search specification.

Only columns to which the user has access are returned.

Args:
  tab_id: The OID of the table whose records we'll get
  search_: An array of search definition objects.
  limit_: The maximum number of rows we'll return.
  offset_: The number of rows to skip before returning records from following rows

The search definition objects should have the form
  {"attnum": <int>, "literal": <any>}
*/
DECLARE
  records jsonb;
BEGIN
  EXECUTE format(
    $q$
    WITH
    count_cte AS (
      SELECT count(1) AS count FROM %2$I.%3$I %4$s
    ),
    results_cte AS (
      SELECT %1$s FROM %2$I.%3$I %4$s %7$s LIMIT %5$L OFFSET %6$L
    ),
    summary_cte_self AS (%8$s)
    %9$s,
    summary_cte AS ( SELECT %11$s FROM results_cte %10$s ),
    summaries_json_cte AS (
      SELECT
        jsonb_build_object(
          'linked_record_summaries',
          NULLIF(
            to_jsonb(summary_cte) - 'summary_self' - 'count_hack',
            '{}'::jsonb
          ),
          'record_summaries',
          NULLIF(
            to_jsonb(summary_cte) - 'count_hack' -> 'summary_self',
            '{}'::jsonb
          )
        )
      AS sj
      FROM summary_cte
    ),
    results_json_cte AS (
      SELECT jsonb_build_object(
        'results', coalesce(jsonb_agg(row_to_json(results_cte.*)), jsonb_build_array()),
        'count', coalesce(max(count_cte.count), 0)
      ) AS rj
      FROM results_cte CROSS JOIN count_cte
    )
    SELECT results_json_cte.rj || summaries_json_cte.sj
    FROM results_json_cte, summaries_json_cte;
    $q$,
    /* %1 */ COALESCE(msar.build_selectable_column_expr(tab_id), 'NULL'),
    /* %2 */ msar.get_relation_schema_name(tab_id),
    /* %3 */ msar.get_relation_name(tab_id),
    /* %4 */ 'WHERE ' || msar.get_score_expr(tab_id, search_) || ' > 0',
    /* %5 */ limit_,
    /* %6 */ offset_,
    /* %7 */ 'ORDER BY ' || NULLIF(
      concat(
        msar.get_score_expr(tab_id, search_) || ' DESC, ',
        msar.build_total_order_expr(tab_id, null)
      ),
      ''
    ),
    /* %8 */ msar.build_record_summary_query_for_table(
      tab_id,
      msar.get_selectable_pkey_attnum(tab_id),
      table_record_summary_templates
    ),
    /* %9 */ msar.build_linked_record_summaries_ctes(tab_id),
    /* %10 */ msar.build_summary_join_expr_for_table(tab_id, 'results_cte'),
    /* %11 */ COALESCE(
      NULLIF(
        concat_ws(', ',
          msar.build_summary_json_expr_for_table(tab_id),
          CASE WHEN return_record_summaries
          THEN msar.build_self_summary_json_expr(tab_id)
          END
        ), ''
      ), 'COUNT(1) AS count_hack'
      -- count_hack ensures that summary_cte is not empty,
      -- which in turn helps to generate summaries_json_cte
    )
  ) INTO records;
  RETURN records;
END;
$_$;


--
-- Name: set_col_default(regclass, smallint, text); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.set_col_default(tab_id regclass, col_id smallint, default_ text) RETURNS text
    LANGUAGE plpgsql STRICT
    AS $$/*
Sets the default for a given column, returning the text of the expression executed.

Args:
  tab_id: The OID of the table containing the column whose default we'll alter.
  col_id: The attnum of the column whose default we'll alter.
  default_: The desired default.
*/
DECLARE
  col_default_sql text;
BEGIN
  SELECT format(
    'ALTER TABLE %I.%I ALTER COLUMN %I SET DEFAULT %L',
    msar.get_relation_schema_name(tab_id),
    msar.get_relation_name(tab_id),
    msar.get_column_name(tab_id, col_id),
    default_
  ) INTO col_default_sql;
  EXECUTE col_default_sql;
  RETURN col_default_sql;
END;
$$;


--
-- Name: set_members_to_role(regrole, oid[]); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.set_members_to_role(parent_rol_id regrole, members oid[]) RETURNS jsonb
    LANGUAGE plpgsql STRICT
    AS $$/*
Grant/Revoke direct membership to/from roles.

Returns a json object describing the updated information of the parent role.

  {
    "oid": <int>
    "name": <str>
    "super": <bool>
    "inherits": <bool>
    "create_role": <bool>
    "create_db": <bool>
    "login": <bool>
    "description": <str|null>
    "members": <[
        { "oid": <int>, "admin": <bool> }
      ]|null>
  }

Args:
  parent_rol_id: The OID of role whose membership will be granted/revoked to/from other roles.
  members: An array of role OID(s) whom we want to grant direct membership of the parent role.
           Only the OID(s) present in the array will be granted membership of parent role,
           Membership will be revoked for existing members not present in this array.
*/
DECLARE
  parent_role_name text := msar.get_role_name(parent_rol_id);
  parent_role_info jsonb := msar.get_role(parent_role_name);
  all_members_array bigint[];
  revoke_members_array bigint[];
  set_members_expr text;
BEGIN
  -- Get all the members of parent_role.
  SELECT array_agg(x.oid)
    FROM jsonb_to_recordset(
      CASE WHEN parent_role_info ->> 'members' IS NOT NULL
      THEN parent_role_info -> 'members'
      ELSE NULL END
    ) AS x(oid oid, admin boolean)
  INTO all_members_array;
  -- Find all the roles whose membership we want to revoke.
  SELECT ARRAY(
    SELECT unnest(all_members_array)
    EXCEPT
    SELECT unnest(members)
  ) INTO revoke_members_array;
  -- REVOKE/GRANT membership for parent_role.
  set_members_expr := concat_ws(
    E'\n',
    msar.build_revoke_membership_expr(parent_rol_id, revoke_members_array),
    msar.build_grant_membership_expr(parent_rol_id, members)
  );
  EXECUTE set_members_expr;
  -- Return the updated parent_role info including membership details.
  RETURN msar.get_role(parent_role_name);
END;
$$;


--
-- Name: set_not_null(regclass, smallint, boolean); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.set_not_null(tab_id regclass, col_id smallint, not_null boolean) RETURNS text
    LANGUAGE plpgsql STRICT
    AS $$/*
Alter a column's NOT NULL setting, returning the text of the expression executed.

Args:
  tab_id: The OID of the table containing the column whose nullability we'll alter.
  col_id: The attnum of the column whose nullability we'll alter.
  not_null: If true, we 'SET NOT NULL'. If false, we 'DROP NOT NULL' if null, we do nothing.
*/
DECLARE
  not_null_sql text;
BEGIN
  SELECT format(
    'ALTER TABLE %I.%I ALTER COLUMN %I %s NOT NULL',
    msar.get_relation_schema_name(tab_id),
    msar.get_relation_name(tab_id),
    msar.get_column_name(tab_id, col_id),
    CASE WHEN not_null THEN 'SET' ELSE 'DROP' END
  ) INTO not_null_sql;
  EXECUTE not_null_sql;
  RETURN not_null_sql;
END;
$$;


--
-- Name: set_old_col_default(regclass, smallint, text, text, boolean, jsonb); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.set_old_col_default(tab_id regclass, col_id smallint, old_default text, new_type text, is_default_dynamic boolean, cast_options jsonb DEFAULT '{}'::jsonb) RETURNS text
    LANGUAGE plpgsql STRICT
    AS $$/*
Sets the old default for a given column, returning the text of the expression executed.

Args:
  tab_id: The OID of the table containing the column whose default we'll alter.
  col_id: The attnum of the column whose default we'll alter.
  old_default: The current default. In some cases in the context of the caller, we want to reset the
               original default, but cast to a new type.
  new_type: The target type to which we'll cast the new default.
  is_default_dynamic: Whether the current default is dynamic, can be obtained with msar.is_default_possibly_dynamic.
  cast_options: Suggestions to be used while type casting.
*/
DECLARE
  default_ text;
  default_expr text;
BEGIN
  IF is_default_dynamic THEN
    default_ := format('%s::%s', old_default, new_type);
  ELSE
    EXECUTE format('SELECT %s', msar.build_cast_expr(old_default, new_type, cast_options)) INTO default_;
    default_ := quote_literal(default_);
  END IF;

  default_expr := format(
    'ALTER TABLE %I.%I ALTER COLUMN %I SET DEFAULT %s',
    msar.get_relation_schema_name(tab_id),
    msar.get_relation_name(tab_id),
    msar.get_column_name(tab_id, col_id),
    default_
  );
  EXECUTE default_expr;
  RETURN default_expr;
END;
$$;


--
-- Name: set_pkey_column(regclass, integer, msar.pkey_kind, boolean); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.set_pkey_column(tab_id regclass, col_id integer, default_type msar.pkey_kind DEFAULT NULL::msar.pkey_kind, drop_old_pkey_col boolean DEFAULT false) RETURNS void
    LANGUAGE plpgsql
    AS $_$/*
Set a primary key column with an optional predefined default on a table.

Args:
  tab_id: This is the OID or name of the table for which we'll set the primary key column.
  col_id: This is the attnum of the column we'll set as the primary key column.
  pkey_type: The "type" of the pkey column. 'UUIDv4' means a `uuid` column using the
             `gen_random_uuid()` function for its default values. 'IDENTITY' means an `integer`
             using `GENERATED BY DEFAULT AS IDENTITY` for default values. If `null` is passed, we
             do not set any default, or change the column type.
  drop_old_pkey_col: Whether we should drop the current primary key column during this operation.
*/
BEGIN
  IF drop_old_pkey_col THEN
    PERFORM msar.drop_columns(tab_id, msar.get_pk_column(tab_id));
  END IF;
  PERFORM msar.drop_constraint(tab_id, oid)
    FROM pg_catalog.pg_constraint WHERE conrelid=tab_id AND contype='p';
  PERFORM msar.add_constraints(
    tab_id,
    jsonb_build_array(jsonb_build_object('type', 'p', 'columns', jsonb_build_array(col_id)))
  );
  IF default_type IS NOT NULL THEN
    EXECUTE format(
      CASE WHEN default_type = 'IDENTITY' AND attidentity = '' THEN
        $s$
        ALTER TABLE %1$I.%2$I
          ALTER COLUMN %3$I TYPE integer USING msar.cast_to_integer(%3$I),
          ALTER COLUMN %3$I ADD GENERATED BY DEFAULT AS IDENTITY;
        SELECT setval(pg_catalog.pg_get_serial_sequence('%1$I.%2$I', '%3$s'), max(%3$I)) FROM %1$I.%2$I;
        $s$
      WHEN default_type = 'UUIDv4' THEN
        'ALTER TABLE %1$I.%2$I ALTER COLUMN %3$I SET DEFAULT gen_random_uuid();'
      ELSE
        ''
      END,
      msar.get_relation_schema_name(tab_id),
      msar.get_relation_name(tab_id),
      msar.get_column_name(tab_id, col_id)
    ) FROM pg_catalog.pg_attribute WHERE attrelid=tab_id AND attnum=col_id;
  END IF;
END;
$_$;


--
-- Name: set_schema_description(oid, text); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.set_schema_description(sch_id oid, description text) RETURNS void
    LANGUAGE plpgsql
    AS $$/*
Set the PostgreSQL description (aka COMMENT) of a schema.

Descriptions are removed by passing an empty string or NULL.

Args:
  sch_id: The OID of the schema.
  description: The new description, UNQUOTED
*/
BEGIN
  EXECUTE format('COMMENT ON SCHEMA %I IS %L', msar.get_schema_name(sch_id), description);
END;
$$;


--
-- Name: table_info_table(); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.table_info_table() RETURNS TABLE(oid bigint, name name, schema bigint, description text, owner_oid bigint, current_role_priv jsonb, current_role_owns boolean, type text)
    LANGUAGE sql STABLE
    AS $$
SELECT
  oid::bigint AS oid,
  relname AS name,
  relnamespace::bigint AS schema,
  msar.obj_description(oid, 'pg_class') AS description,
  relowner::bigint AS owner_oid,
  msar.list_table_privileges_for_current_role(oid) AS current_role_priv,
  pg_catalog.pg_has_role(relowner, 'USAGE') AS current_role_owns,
  CASE relkind
    WHEN 'r' THEN 'table'
    WHEN 'v' THEN 'view'
    WHEN 'm' THEN 'materialized_view'
  END::text AS type
FROM pg_catalog.pg_class
WHERE relkind = 'r' OR relkind = 'v' OR relkind = 'm';
$$;


--
-- Name: time_to_degrees(time without time zone); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.time_to_degrees(time_ time without time zone) RETURNS double precision
    LANGUAGE sql
    AS $$/*
Convert the given time to degrees (on a 24 hour clock, indexed from midnight).

To get the fraction of 86400 seconds passed, we divide time_ by 86400 and then 
to get the equivalent fraction of 360°, we multiply by 360, which is equivalent
to divide by 240. 

Examples:
  00:00:00 =>   0
  06:00:00 =>  90
  12:00:00 => 180
  18:00:00 => 270
*/
SELECT EXTRACT(EPOCH FROM time_) / 240;
$$;


--
-- Name: transfer_database_ownership(regrole); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.transfer_database_ownership(new_owner_oid regrole) RETURNS jsonb
    LANGUAGE plpgsql STRICT
    AS $$/*
Transfers ownership of the current database to a new owner.

Args:
  new_owner_oid: The OID of the role whom we want to be the new owner of the current database.

NOTE: To successfully transfer ownership of a database to a new owner the current user must:
  - Be a Superuser/Owner of the current database.
  - Be a `MEMBER` of the new owning role. i.e. The current role should be able to `SET ROLE`
    to the new owning role.
  - Have `CREATEDB` privilege.
*/
BEGIN
  EXECUTE format(
    'ALTER DATABASE %I OWNER TO %I',
    pg_catalog.current_database(),
    msar.get_role_name(new_owner_oid)
  );
  RETURN msar.get_current_database_info();
END;
$$;


--
-- Name: transfer_schema_ownership(regnamespace, regrole); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.transfer_schema_ownership(sch_id regnamespace, new_owner_oid regrole) RETURNS jsonb
    LANGUAGE plpgsql STRICT
    AS $$/*
Transfers ownership of a given schema to a new owner.

Args:
  sch_id: The OID of the schema to transfer.
  new_owner_oid: The OID of the role whom we want to be the new owner of the schema.

NOTE: To successfully transfer ownership of a schema to a new owner the current user must:
  - Be a Superuser/Owner of the schema.
  - Be a `MEMBER` of the new owning role. i.e. The current role should be able to `SET ROLE`
    to the new owning role.
  - Have `CREATE` privilege for the database.
*/
BEGIN
  EXECUTE format(
    'ALTER SCHEMA %I OWNER TO %I',
    msar.get_schema_name(sch_id),
    msar.get_role_name(new_owner_oid)
  );
  RETURN msar.get_schema(sch_id);
END;
$$;


--
-- Name: transfer_table_ownership(regclass, regrole); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.transfer_table_ownership(tab_id regclass, new_owner_oid regrole) RETURNS jsonb
    LANGUAGE plpgsql STRICT
    AS $$/*
Transfers ownership of a given table to a new owner.

Args:
  tab_id: The OID of the table to transfer.
  new_owner_oid: The OID of the role whom we want to be the new owner of the table.

NOTE: To successfully transfer ownership of a table to a new owner the current user must:
  - Be a Superuser/Owner of the table.
  - Be a `MEMBER` of the new owning role. i.e. The current role should be able to `SET ROLE`
    to the new owning role.
  - Have `CREATE` privilege on the table's schema.
*/
BEGIN
  EXECUTE format(
    'ALTER TABLE %I.%I OWNER TO %I',
    msar.get_relation_schema_name(tab_id),
    msar.get_relation_name(tab_id),
    msar.get_role_name(new_owner_oid)
  );
  RETURN msar.get_table(tab_id);
END;
$$;


--
-- Name: uri_authority(text); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.uri_authority(text) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
    SELECT (msar.uri_parts($1))[4];
$_$;


--
-- Name: uri_fragment(text); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.uri_fragment(text) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
    SELECT (msar.uri_parts($1))[9];
$_$;


--
-- Name: uri_parts(text); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.uri_parts(text) RETURNS text[]
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
    SELECT regexp_match($1, '^(([^:/?#]+):)?(//([^/?#]*))?([^?#]*)(\?([^#]*))?(#(.*))?');
$_$;


--
-- Name: uri_path(text); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.uri_path(text) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
    SELECT (msar.uri_parts($1))[5];
$_$;


--
-- Name: uri_query(text); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.uri_query(text) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
    SELECT (msar.uri_parts($1))[7];
$_$;


--
-- Name: uri_scheme(text); Type: FUNCTION; Schema: msar; Owner: -
--

CREATE FUNCTION msar.uri_scheme(text) RETURNS text
    LANGUAGE sql IMMUTABLE STRICT
    AS $_$
    SELECT (msar.uri_parts($1))[2];
$_$;


--
-- Name: peak_month(date); Type: AGGREGATE; Schema: msar; Owner: -
--

CREATE AGGREGATE msar.peak_month(date) (
    SFUNC = msar.add_month_to_vector,
    STYPE = point,
    INITCOND = '(0,0)',
    FINALFUNC = msar.point_to_month
);


--
-- Name: peak_time(time without time zone); Type: AGGREGATE; Schema: msar; Owner: -
--

CREATE AGGREGATE msar.peak_time(time without time zone) (
    SFUNC = msar.add_time_to_vector,
    STYPE = point,
    INITCOND = '(0,0)',
    FINALFUNC = msar.point_to_time
);


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: expr_templates; Type: TABLE; Schema: msar; Owner: -
--

CREATE TABLE msar.expr_templates (
    expr_key text NOT NULL,
    expr_template text
);


--
-- Name: top_level_domains; Type: TABLE; Schema: msar; Owner: -
--

CREATE TABLE msar.top_level_domains (
    tld text NOT NULL
);


--
-- Name: chats; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.chats (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    user_id uuid NOT NULL,
    title character varying(255),
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    project_id uuid
);


--
-- Name: chunks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.chunks (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    page_version_id uuid NOT NULL,
    document_id uuid NOT NULL,
    chunk_index integer NOT NULL,
    chunk_type character varying(20) NOT NULL,
    content text NOT NULL,
    context_prefix text,
    heading_path jsonb DEFAULT '[]'::jsonb,
    token_count integer,
    chunk_hash character varying(64) NOT NULL,
    embedding_status character varying(50) DEFAULT 'PENDING'::character varying,
    embedding_model character varying(100),
    embedding_dimension integer,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    embedding public.halfvec(2048),
    CONSTRAINT chk_chunk_type CHECK (((chunk_type)::text = ANY ((ARRAY['text'::character varying, 'table'::character varying, 'list'::character varying, 'code'::character varying, 'quote'::character varying, 'mixed'::character varying])::text[]))),
    CONSTRAINT chk_embedding_status CHECK (((embedding_status)::text = ANY ((ARRAY['PENDING'::character varying, 'PROCESSING'::character varying, 'COMPLETED'::character varying, 'FAILED'::character varying])::text[])))
);


--
-- Name: TABLE chunks; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.chunks IS 'Document chunks with embeddings for retrieval';


--
-- Name: crawl_jobs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.crawl_jobs (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    project_id uuid NOT NULL,
    status character varying(50) DEFAULT 'DISCOVERED'::character varying NOT NULL,
    start_url text NOT NULL,
    max_pages integer DEFAULT 100,
    max_depth integer DEFAULT 3,
    max_response_size integer DEFAULT 10485760,
    max_total_bytes integer DEFAULT 1073741824,
    max_concurrent_requests integer DEFAULT 5,
    max_browser_pages integer DEFAULT 1,
    request_timeout integer DEFAULT 30,
    browser_timeout integer DEFAULT 60,
    per_domain_concurrency integer DEFAULT 2,
    started_at timestamp with time zone,
    completed_at timestamp with time zone,
    error_message text,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_crawl_job_status CHECK (((status)::text = ANY ((ARRAY['DISCOVERED'::character varying, 'FETCHING'::character varying, 'FETCHED'::character varying, 'PROCESSING'::character varying, 'PROCESSED'::character varying, 'CHUNKING'::character varying, 'CHUNKED'::character varying, 'EMBEDDING'::character varying, 'INDEXED'::character varying, 'FETCH_FAILED'::character varying, 'PROCESSING_FAILED'::character varying, 'CHUNKING_FAILED'::character varying, 'EMBEDDING_FAILED'::character varying])::text[])))
);


--
-- Name: TABLE crawl_jobs; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.crawl_jobs IS 'Crawl jobs with configurable limits and lifecycle tracking';


--
-- Name: crawl_urls; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.crawl_urls (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    crawl_job_id uuid NOT NULL,
    url text NOT NULL,
    normalized_url text NOT NULL,
    depth integer DEFAULT 0,
    status character varying(20) DEFAULT 'PENDING'::character varying,
    discovered_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    fetched_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_crawl_url_status CHECK (((status)::text = ANY ((ARRAY['PENDING'::character varying, 'QUEUED'::character varying, 'FETCHING'::character varying, 'FETCHED'::character varying, 'FAILED'::character varying])::text[])))
);


--
-- Name: TABLE crawl_urls; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.crawl_urls IS 'URL frontier for crawl job processing';


--
-- Name: documents; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.documents (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    page_version_id uuid NOT NULL,
    content text NOT NULL,
    content_format character varying(20) DEFAULT 'markdown'::character varying,
    metadata jsonb DEFAULT '{}'::jsonb,
    processing_status character varying(50) DEFAULT 'PENDING'::character varying,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    cleaned_content text,
    processed_at timestamp with time zone,
    CONSTRAINT chk_doc_processing_status CHECK (((processing_status)::text = ANY ((ARRAY['PENDING'::character varying, 'PROCESSING'::character varying, 'COMPLETED'::character varying, 'FAILED'::character varying])::text[])))
);


--
-- Name: TABLE documents; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.documents IS 'Cleaned/processed document content from a page version';


--
-- Name: media_assets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.media_assets (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    page_version_id uuid NOT NULL,
    document_id uuid NOT NULL,
    media_type character varying(20) NOT NULL,
    source_url text,
    mime_type character varying(100),
    data_base64 text,
    description text NOT NULL,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_media_type CHECK (((media_type)::text = ANY ((ARRAY['image'::character varying, 'table'::character varying])::text[])))
);


--
-- Name: messages; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.messages (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    chat_id uuid NOT NULL,
    user_id uuid NOT NULL,
    role character varying(20) NOT NULL,
    content text NOT NULL,
    is_url boolean DEFAULT false,
    url_processed boolean DEFAULT false,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_role CHECK (((role)::text = ANY ((ARRAY['user'::character varying, 'assistant'::character varying])::text[])))
);


--
-- Name: page_versions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.page_versions (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    page_id uuid NOT NULL,
    status_code integer,
    content_type character varying(255),
    raw_content_location text,
    content_hash character varying(64),
    fetch_method character varying(20) NOT NULL,
    response_size integer DEFAULT 0,
    fetched_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    processing_status character varying(50) DEFAULT 'PENDING'::character varying,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_fetch_method CHECK (((fetch_method)::text = ANY ((ARRAY['httpx'::character varying, 'playwright'::character varying])::text[]))),
    CONSTRAINT chk_processing_status CHECK (((processing_status)::text = ANY ((ARRAY['PENDING'::character varying, 'PROCESSING'::character varying, 'COMPLETED'::character varying, 'FAILED'::character varying])::text[])))
);


--
-- Name: TABLE page_versions; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.page_versions IS 'Individual fetches/versions of a page with raw content metadata';


--
-- Name: pages; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.pages (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    project_id uuid NOT NULL,
    url text NOT NULL,
    normalized_url text NOT NULL,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    chat_id uuid
);


--
-- Name: TABLE pages; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.pages IS 'Logical pages (URLs) within a project scope';


--
-- Name: COLUMN pages.chat_id; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.pages.chat_id IS 'Chat that this page belongs to';


--
-- Name: processing_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.processing_events (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    entity_type character varying(50) NOT NULL,
    entity_id uuid NOT NULL,
    event_type character varying(50) NOT NULL,
    status character varying(20) NOT NULL,
    worker_id character varying(255),
    task_id character varying(255),
    message text,
    metadata jsonb DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_entity_type CHECK (((entity_type)::text = ANY ((ARRAY['page_version'::character varying, 'document'::character varying, 'chunk'::character varying])::text[])))
);


--
-- Name: TABLE processing_events; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.processing_events IS 'Audit trail of processing events for debugging';


--
-- Name: projects; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.projects (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    user_id uuid NOT NULL,
    name character varying(255) NOT NULL,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: TABLE projects; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.projects IS 'User projects representing knowledge bases/crawl scopes';


--
-- Name: users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.users (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    email character varying(255) NOT NULL,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: TABLE users; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.users IS 'Application users who own projects and data';


--
-- Name: worker_failures; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.worker_failures (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    entity_type character varying(50) NOT NULL,
    entity_id uuid NOT NULL,
    worker_id character varying(255),
    task_id character varying(255),
    error_type character varying(255),
    error_message text,
    retry_count integer DEFAULT 0,
    max_retries integer DEFAULT 3,
    is_resolved boolean DEFAULT false,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    resolved_at timestamp with time zone,
    CONSTRAINT chk_failure_entity_type CHECK (((entity_type)::text = ANY ((ARRAY['page_version'::character varying, 'document'::character varying, 'chunk'::character varying])::text[])))
);


--
-- Name: TABLE worker_failures; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON TABLE public.worker_failures IS 'Track worker failures for retry and recovery';


--
-- Name: expr_templates expr_templates_pkey; Type: CONSTRAINT; Schema: msar; Owner: -
--

ALTER TABLE ONLY msar.expr_templates
    ADD CONSTRAINT expr_templates_pkey PRIMARY KEY (expr_key);


--
-- Name: top_level_domains top_level_domains_pkey; Type: CONSTRAINT; Schema: msar; Owner: -
--

ALTER TABLE ONLY msar.top_level_domains
    ADD CONSTRAINT top_level_domains_pkey PRIMARY KEY (tld);


--
-- Name: chats chats_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chats
    ADD CONSTRAINT chats_pkey PRIMARY KEY (id);


--
-- Name: chunks chunks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chunks
    ADD CONSTRAINT chunks_pkey PRIMARY KEY (id);


--
-- Name: crawl_jobs crawl_jobs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.crawl_jobs
    ADD CONSTRAINT crawl_jobs_pkey PRIMARY KEY (id);


--
-- Name: crawl_urls crawl_urls_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.crawl_urls
    ADD CONSTRAINT crawl_urls_pkey PRIMARY KEY (id);


--
-- Name: documents documents_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.documents
    ADD CONSTRAINT documents_pkey PRIMARY KEY (id);


--
-- Name: media_assets media_assets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.media_assets
    ADD CONSTRAINT media_assets_pkey PRIMARY KEY (id);


--
-- Name: messages messages_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.messages
    ADD CONSTRAINT messages_pkey PRIMARY KEY (id);


--
-- Name: page_versions page_versions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.page_versions
    ADD CONSTRAINT page_versions_pkey PRIMARY KEY (id);


--
-- Name: pages pages_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pages
    ADD CONSTRAINT pages_pkey PRIMARY KEY (id);


--
-- Name: processing_events processing_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.processing_events
    ADD CONSTRAINT processing_events_pkey PRIMARY KEY (id);


--
-- Name: projects projects_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.projects
    ADD CONSTRAINT projects_pkey PRIMARY KEY (id);


--
-- Name: chunks uq_chunk_document_index; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chunks
    ADD CONSTRAINT uq_chunk_document_index UNIQUE (document_id, chunk_index);


--
-- Name: chunks uq_chunk_page_hash; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chunks
    ADD CONSTRAINT uq_chunk_page_hash UNIQUE (page_version_id, chunk_hash);


--
-- Name: CONSTRAINT uq_chunk_page_hash ON chunks; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON CONSTRAINT uq_chunk_page_hash ON public.chunks IS 'Prevents duplicate chunks within same page version for idempotent processing';


--
-- Name: crawl_urls uq_crawl_job_url; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.crawl_urls
    ADD CONSTRAINT uq_crawl_job_url UNIQUE (crawl_job_id, normalized_url);


--
-- Name: documents uq_document_page_version; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.documents
    ADD CONSTRAINT uq_document_page_version UNIQUE (page_version_id);


--
-- Name: users users_email_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_email_key UNIQUE (email);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- Name: worker_failures worker_failures_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.worker_failures
    ADD CONSTRAINT worker_failures_pkey PRIMARY KEY (id);


--
-- Name: idx_chats_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_chats_created_at ON public.chats USING btree (created_at);


--
-- Name: idx_chats_project_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_chats_project_id ON public.chats USING btree (project_id);


--
-- Name: idx_chats_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_chats_user_id ON public.chats USING btree (user_id);


--
-- Name: idx_chunks_chunk_hash; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_chunks_chunk_hash ON public.chunks USING btree (chunk_hash);


--
-- Name: idx_chunks_document_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_chunks_document_id ON public.chunks USING btree (document_id);


--
-- Name: idx_chunks_embedding; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_chunks_embedding ON public.chunks USING ivfflat (embedding public.halfvec_cosine_ops);


--
-- Name: idx_chunks_embedding_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_chunks_embedding_status ON public.chunks USING btree (embedding_status);


--
-- Name: idx_chunks_page_version_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_chunks_page_version_id ON public.chunks USING btree (page_version_id);


--
-- Name: idx_crawl_jobs_project_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_crawl_jobs_project_id ON public.crawl_jobs USING btree (project_id);


--
-- Name: idx_crawl_jobs_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_crawl_jobs_status ON public.crawl_jobs USING btree (status);


--
-- Name: idx_crawl_urls_crawl_job_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_crawl_urls_crawl_job_id ON public.crawl_urls USING btree (crawl_job_id);


--
-- Name: idx_crawl_urls_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_crawl_urls_status ON public.crawl_urls USING btree (status);


--
-- Name: idx_documents_page_version_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_documents_page_version_id ON public.documents USING btree (page_version_id);


--
-- Name: idx_media_assets_document_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_media_assets_document_id ON public.media_assets USING btree (document_id);


--
-- Name: idx_media_assets_page_version_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_media_assets_page_version_id ON public.media_assets USING btree (page_version_id);


--
-- Name: idx_messages_chat_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_messages_chat_id ON public.messages USING btree (chat_id);


--
-- Name: idx_messages_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_messages_created_at ON public.messages USING btree (created_at);


--
-- Name: idx_messages_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_messages_user_id ON public.messages USING btree (user_id);


--
-- Name: idx_page_versions_content_hash; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_page_versions_content_hash ON public.page_versions USING btree (content_hash);


--
-- Name: idx_page_versions_page_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_page_versions_page_id ON public.page_versions USING btree (page_id);


--
-- Name: idx_page_versions_processing_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_page_versions_processing_status ON public.page_versions USING btree (processing_status);


--
-- Name: idx_pages_chat_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_pages_chat_id ON public.pages USING btree (chat_id);


--
-- Name: idx_pages_normalized_url; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_pages_normalized_url ON public.pages USING btree (normalized_url);


--
-- Name: idx_pages_project_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_pages_project_id ON public.pages USING btree (project_id);


--
-- Name: idx_pages_project_normalized_url; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_pages_project_normalized_url ON public.pages USING btree (project_id, normalized_url);


--
-- Name: idx_processing_events_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_processing_events_created_at ON public.processing_events USING btree (created_at);


--
-- Name: idx_processing_events_entity; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_processing_events_entity ON public.processing_events USING btree (entity_type, entity_id);


--
-- Name: idx_projects_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_projects_user_id ON public.projects USING btree (user_id);


--
-- Name: idx_users_email; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_users_email ON public.users USING btree (email);


--
-- Name: idx_worker_failures_entity; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_worker_failures_entity ON public.worker_failures USING btree (entity_type, entity_id);


--
-- Name: idx_worker_failures_is_resolved; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_worker_failures_is_resolved ON public.worker_failures USING btree (is_resolved);


--
-- Name: chats chats_project_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chats
    ADD CONSTRAINT chats_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.projects(id) ON DELETE CASCADE;


--
-- Name: chats chats_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chats
    ADD CONSTRAINT chats_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: chunks chunks_document_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chunks
    ADD CONSTRAINT chunks_document_id_fkey FOREIGN KEY (document_id) REFERENCES public.documents(id) ON DELETE CASCADE;


--
-- Name: chunks chunks_page_version_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chunks
    ADD CONSTRAINT chunks_page_version_id_fkey FOREIGN KEY (page_version_id) REFERENCES public.page_versions(id) ON DELETE CASCADE;


--
-- Name: crawl_jobs crawl_jobs_project_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.crawl_jobs
    ADD CONSTRAINT crawl_jobs_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.projects(id) ON DELETE CASCADE;


--
-- Name: crawl_urls crawl_urls_crawl_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.crawl_urls
    ADD CONSTRAINT crawl_urls_crawl_job_id_fkey FOREIGN KEY (crawl_job_id) REFERENCES public.crawl_jobs(id) ON DELETE CASCADE;


--
-- Name: documents documents_page_version_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.documents
    ADD CONSTRAINT documents_page_version_id_fkey FOREIGN KEY (page_version_id) REFERENCES public.page_versions(id) ON DELETE CASCADE;


--
-- Name: chats fk_chat_user; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chats
    ADD CONSTRAINT fk_chat_user FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: chunks fk_chunk_document; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chunks
    ADD CONSTRAINT fk_chunk_document FOREIGN KEY (document_id) REFERENCES public.documents(id) ON DELETE CASCADE;


--
-- Name: chunks fk_chunk_page_version; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.chunks
    ADD CONSTRAINT fk_chunk_page_version FOREIGN KEY (page_version_id) REFERENCES public.page_versions(id) ON DELETE CASCADE;


--
-- Name: crawl_jobs fk_crawl_job_project; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.crawl_jobs
    ADD CONSTRAINT fk_crawl_job_project FOREIGN KEY (project_id) REFERENCES public.projects(id) ON DELETE CASCADE;


--
-- Name: crawl_urls fk_crawl_url_job; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.crawl_urls
    ADD CONSTRAINT fk_crawl_url_job FOREIGN KEY (crawl_job_id) REFERENCES public.crawl_jobs(id) ON DELETE CASCADE;


--
-- Name: documents fk_document_page_version; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.documents
    ADD CONSTRAINT fk_document_page_version FOREIGN KEY (page_version_id) REFERENCES public.page_versions(id) ON DELETE CASCADE;


--
-- Name: pages fk_page_project; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pages
    ADD CONSTRAINT fk_page_project FOREIGN KEY (project_id) REFERENCES public.projects(id) ON DELETE CASCADE;


--
-- Name: page_versions fk_page_version_page; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.page_versions
    ADD CONSTRAINT fk_page_version_page FOREIGN KEY (page_id) REFERENCES public.pages(id) ON DELETE CASCADE;


--
-- Name: projects fk_project_user; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.projects
    ADD CONSTRAINT fk_project_user FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: media_assets media_assets_document_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.media_assets
    ADD CONSTRAINT media_assets_document_id_fkey FOREIGN KEY (document_id) REFERENCES public.documents(id) ON DELETE CASCADE;


--
-- Name: media_assets media_assets_page_version_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.media_assets
    ADD CONSTRAINT media_assets_page_version_id_fkey FOREIGN KEY (page_version_id) REFERENCES public.page_versions(id) ON DELETE CASCADE;


--
-- Name: messages messages_chat_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.messages
    ADD CONSTRAINT messages_chat_id_fkey FOREIGN KEY (chat_id) REFERENCES public.chats(id) ON DELETE CASCADE;


--
-- Name: messages messages_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.messages
    ADD CONSTRAINT messages_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: page_versions page_versions_page_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.page_versions
    ADD CONSTRAINT page_versions_page_id_fkey FOREIGN KEY (page_id) REFERENCES public.pages(id) ON DELETE CASCADE;


--
-- Name: pages pages_chat_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pages
    ADD CONSTRAINT pages_chat_id_fkey FOREIGN KEY (chat_id) REFERENCES public.chats(id) ON DELETE CASCADE;


--
-- Name: pages pages_project_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pages
    ADD CONSTRAINT pages_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.projects(id) ON DELETE CASCADE;


--
-- Name: projects projects_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.projects
    ADD CONSTRAINT projects_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- PostgreSQL database dump complete
--


