# datasette-big-local

[![Tests](https://github.com/simonw/datasette-big-local/workflows/Test/badge.svg)](https://github.com/simonw/datasette-big-local/actions?query=workflow%3ATest)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](https://github.com/simonw/datasette-big-local/blob/main/LICENSE)

This plugin is not useful to anyone outside of the Big Local News team. This repo is public so that the code can be consulted by anyone who wants to know how it works.

## Installation

Install this plugin in the same environment as Datasette, using the URL to the zip file for this repo:

    datasette install https://github.com/simonw/datasette-big-local/archive/refs/heads/main.zip

## Configuration

This plugin takes a single plugin configuration option: the path to the directory where the databases it creates should be stored. In `metadata.yml` that looks like this:

```yaml
plugins:
  datasette-big-local:
    root_dir: /path/to/directory
```
Then start Datasette with `datasette -m metadata.yml`.

### Additional plugin options

- `graphql_url` - the URL to the GraphQL API that this communicates with. This defaults to `https://api.biglocalnews.org/graphql` - you can change this to point at a development instance.
- `csv_size_limit_mb` - the maximum size of CSV file that can be imported, as an integer number of MBs. This defaults to 100MB.

```yaml
plugins:
  datasette-big-local:
    root_dir: /path/to/directory
    graphql_url: https://api.biglocalnews.dev/graphql
    csv_size_limit_mb: 50
```

## Endpoints

This plugin adds some endpoints which are designed to be called from the Big Local News web application.

### /-/big-local-open

When a user clicks "open in Datasette" on a CSV file within Big Local, their browser should submit an HTTP POST to this endpoint with the three following form parameters:

- `project_id` - the Base 64 encoded ID of the project on Big Local, e.g. `UHJvamVjdDpmZjAxNTBjNi1iNjM0LTQ3MmEtODFiMi1lZjJlMGMwMWQyMjQ=`
- `filename` - the name of the CSV file within that project, e.g. `universities_final.csv`
- `remember_token` - a Big Local authentication token for the user who is opening that file - the same value that is stored in that user's `remember_token` cookie

The endpoint will use that `remember_token` cookie to confirm that the user has access to that project.

If they do, Datasette will fetch the content of the CSV file and import it into a SQLite database dedicated to that project.

The database will use the UUID of the project as its name. It will be created on disk if it does not already exist.

The user will also get a signed cookie signing them into the Datasette instance.

Datasette will cache the fact that the user has permission to access that project for five minutes. After five minutes another call will be made to the Big Local GraphQL API to confirm that the user still has permissions for that project.

### /-/big-local-project

There are some situations in which a user may want to open a project directly in Datasette without first selecting a file. This POST endpoint provides that capability.

It takes two required form parameters:

- `project_id` - the Base 64 encoded ID of the project
- `remember_token` - a Big Local authentication token for the user

And one optional parameter:

- `redirect_path` - the path to redirect to after the user has been signed in. This must start with a `/` - it defaults to the database page for the project database.

If the user has permission to access that project, they will be signed in and redirected to the `redirect_path`.

As a convenience, this endpoint also fetches and caches a list of files within the project. Any CSV files that are within the CSV size limit and that have not been previously imported will be listed on the database page, with a button to trigger an import.

## Development

To set up this plugin locally, first checkout the code. Then create a new virtual environment:

    cd datasette-big-local
    python3 -mvenv venv
    source venv/bin/activate

Or if you are using `pipenv`:

    pipenv shell

Now install the dependencies and test dependencies:

    pip install -e '.[test]'

To run the tests:

    pytest
