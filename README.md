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

## Usage

The plugin adds an endpoint at `/-/big-local-open` which can be targetted with an HTTP POST containing the following values:

- `project_id` - the Base 64 encoded ID of a project on Big Local
- `filename` - the name of a CSV file within that project
- `remember_token` - a Big Local authentication token for the user who is opening that file

If the `remember_token` is valid for accessing that project, Datasette will fetch the content of that CSV file and import it into a SQLite database.

The database will use the UUID of the project as its name. It will be created on disk if it does not already exist.

The user will also get a signed cookie signing them into the Datasette instance.

The database will only be visible to users who have a cookie that confirms that they have access to the associated project.

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
