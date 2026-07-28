# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

FROM public.ecr.aws/lambda/python:3.13

RUN pip install --no-cache-dir "psycopg2-binary==2.9.12"

COPY index.py schema.sql seed.sql ${LAMBDA_TASK_ROOT}/

CMD ["index.handler"]
