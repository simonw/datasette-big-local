# datasette-big-local

[![PyPI](https://img.shields.io/pypi/v/datasette-big-local.svg)](https://pypi.org/project/datasette-big-local/)
[![Changelog](https://img.shields.io/github/v/release/simonw/datasette-big-local?include_prereleases&label=changelog)](https://github.com/simonw/datasette-big-local/releases)
[![Tests](https://github.com/simonw/datasette-big-local/workflows/Test/badge.svg)](https://github.com/simonw/datasette-big-local/actions?query=workflow%3ATest)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](https://github.com/simonw/datasette-big-local/blob/main/LICENSE)



## Installation

Install this plugin in the same environment as Datasette.

    $ datasette install datasette-big-local

## Usage

Usage instructions go here.

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
