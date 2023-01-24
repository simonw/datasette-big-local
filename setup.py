from setuptools import setup
import os

VERSION = "0.1"


def get_long_description():
    with open(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "README.md"),
        encoding="utf8",
    ) as fp:
        return fp.read()


setup(
    name="datasette-big-local",
    description="",
    long_description=get_long_description(),
    long_description_content_type="text/markdown",
    author="Simon Willison",
    url="https://github.com/simonw/datasette-big-local",
    project_urls={
        "Issues": "https://github.com/simonw/datasette-big-local/issues",
        "CI": "https://github.com/simonw/datasette-big-local/actions",
        "Changelog": "https://github.com/simonw/datasette-big-local/releases",
    },
    license="Apache License, Version 2.0",
    version=VERSION,
    packages=["datasette_big_local"],
    entry_points={"datasette": ["big_local = datasette_big_local"]},
    install_requires=["datasette", "cachetools", "sqlite-utils"],
    extras_require={"test": ["pytest", "pytest-asyncio"]},
    package_data={
        "datasette_big_local": [
            "templates/*.html",
        ]
    },
    python_requires=">=3.7",
)
