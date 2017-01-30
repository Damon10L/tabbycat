============
Contributing
============

Contributions are welcome, and are greatly appreciated! Every little bit helps, and credit will be given. `Join our Facebook group <https://www.facebook.com/groups/tabbycat.debate/>`_ if you have any questions about how to get started contributing.

Bug reports
===========

Please report bugs by opening a new issue in our `GitHub repository <https://github.com/czlee/tabbycat/issues>`_. It is most helpful if you can include:

- How Tabbycat was installed (on Heroku, locally on OS X, `etc.`)
- Any details about your tournament and setup that might be helpful in troubleshooting
- Detailed steps for how to reproduce the bug

Getting started
===============

.. important:: We are using the `git-flow workflow <http://danielkummer.github.io/git-flow-cheatsheet/>`_, so please submit any pull requests against the **develop branch** (and not master).

- Generally we prefer that features and bug fixes are submitted as pull requests on their own branch (as described in the git-flow process)
- We use Django's testing tools — it would be great if new features came with unit tests
- TODO: more detail on tests and pull request checklist/guidelines

Style guide
===========

We use `flake8 <http://flake8.readthedocs.io>`_ to check for a non-strict series of style rules. Warnings will trigger a Travis CI build to fail. The entire codebase can be checked by using::

    $ flake8 .

While in the base directory

Versioning convention
=====================

Our convention is to increment the minor version whenever we add new functionality, and to increment the major version whenever

- the database can't be migrated forwards using ``python manage.py migrate --no-input``, or
- there is a major change to how the tournament workflow goes, or
- we make some other change that is, in our opinion, significant enough to warrant a milestone.

Most of the time, we write `data migrations <https://docs.djangoproject.com/en/1.10/topics/migrations/#data-migrations>`_ to allow existing systems to be upgraded easily. However, we don't always support backward database migrations. Our expectation is that long-lived installations keep up with our latest version.

One day, we hope to have a public API in place to facilitate the integration with other debating tournament software, like registration or adjudicator feedback systems. If and when that happens, we'll probably revise this convention to be more in line with `Semantic Versioning <http://semver.org/>`_.

Starting from version 0.7.0, we use code names for versions, being breeds of cats in alphabetical order.

Documentation
=============

Documentation is created using `Sphinx <http://sphinx-doc.org/>`_ and hosted at `Read The Docs <https://readthedocs.org>`_. Pushes to ``develop`` will update the *latest* documentation set, while pushes to ``master`` will update the *stable* documentation set.

To preview the documentation locally, install the docs-specific requirements (from the base folder)::

    $ pip install -r 'docs/requirements.txt'

Then start the server::

    $ sphinx-autobuild docs docs/_build/html --port 7999

You should then be able to preview the docs at `127.0.0.1:7999 <http://127.0.0.1:7999>`_.
