# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

FROM public.ecr.aws/lambda/python:3.13

RUN pip install --no-cache-dir "psycopg2-binary==2.9.12" "sqlglot==30.12.0"

COPY structured_search_lambda.py ${LAMBDA_TASK_ROOT}/

CMD ["structured_search_lambda.handler"]
