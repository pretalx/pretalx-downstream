# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/pretalx/pretalx-downstream/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                                                        |    Stmts |     Miss |   Branch |   BrPart |   Cover |   Missing |
|------------------------------------------------------------ | -------: | -------: | -------: | -------: | ------: | --------: |
| pretalx\_downstream/\_\_init\_\_.py                         |        1 |        0 |        0 |        0 |    100% |           |
| pretalx\_downstream/apps.py                                 |       15 |        0 |        0 |        0 |    100% |           |
| pretalx\_downstream/forms.py                                |       11 |        0 |        0 |        0 |    100% |           |
| pretalx\_downstream/management/\_\_init\_\_.py              |        0 |        0 |        0 |        0 |    100% |           |
| pretalx\_downstream/management/commands/\_\_init\_\_.py     |        0 |        0 |        0 |        0 |    100% |           |
| pretalx\_downstream/management/commands/downstream\_pull.py |       19 |        0 |        2 |        0 |    100% |           |
| pretalx\_downstream/models.py                               |       17 |        0 |        2 |        0 |    100% |           |
| pretalx\_downstream/signals.py                              |       32 |        4 |       12 |        3 |     84% |26, 32, 36-37, 42->52 |
| pretalx\_downstream/tasks.py                                |      155 |       11 |       52 |        8 |     90% |64->68, 72, 126-127, 163-164, 192, 199->210, 220-226, 234-236, 243->246, 250->256, 252->251 |
| pretalx\_downstream/views.py                                |       37 |        2 |        4 |        0 |     95% |     34-35 |
| **TOTAL**                                                   |  **287** |   **17** |   **72** |   **11** | **92%** |           |


## Setup coverage badge

Below are examples of the badges you can use in your main branch `README` file.

### Direct image

[![Coverage badge](https://raw.githubusercontent.com/pretalx/pretalx-downstream/python-coverage-comment-action-data/badge.svg)](https://htmlpreview.github.io/?https://github.com/pretalx/pretalx-downstream/blob/python-coverage-comment-action-data/htmlcov/index.html)

This is the one to use if your repository is private or if you don't want to customize anything.

### [Shields.io](https://shields.io) Json Endpoint

[![Coverage badge](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/pretalx/pretalx-downstream/python-coverage-comment-action-data/endpoint.json)](https://htmlpreview.github.io/?https://github.com/pretalx/pretalx-downstream/blob/python-coverage-comment-action-data/htmlcov/index.html)

Using this one will allow you to [customize](https://shields.io/endpoint) the look of your badge.
It won't work with private repositories. It won't be refreshed more than once per five minutes.

### [Shields.io](https://shields.io) Dynamic Badge

[![Coverage badge](https://img.shields.io/badge/dynamic/json?color=brightgreen&label=coverage&query=%24.message&url=https%3A%2F%2Fraw.githubusercontent.com%2Fpretalx%2Fpretalx-downstream%2Fpython-coverage-comment-action-data%2Fendpoint.json)](https://htmlpreview.github.io/?https://github.com/pretalx/pretalx-downstream/blob/python-coverage-comment-action-data/htmlcov/index.html)

This one will always be the same color. It won't work for private repos. I'm not even sure why we included it.

## What is that?

This branch is part of the
[python-coverage-comment-action](https://github.com/marketplace/actions/python-coverage-comment)
GitHub Action. All the files in this branch are automatically generated and may be
overwritten at any moment.