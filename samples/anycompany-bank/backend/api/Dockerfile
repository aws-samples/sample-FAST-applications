# Console API as a streaming Lambda.
#
# Two constraints drive this design:
#   - a full assessment takes 25-60s, which exceeds API Gateway's hard 30s
#     integration timeout, so the API is fronted by a Lambda Function URL
#   - Lambda's native response streaming supports Node.js only, so a Python
#     app needs the Lambda Web Adapter to stream through the Runtime API
#
# The adapter runs FastAPI as an ordinary HTTP server and translates Lambda
# invocations into requests against it, forwarding SSE back as chunked
# transfer-encoding.
FROM public.ecr.aws/lambda/python:3.13

# Lambda Web Adapter, as a layer-style copy into /opt/extensions.
COPY --from=public.ecr.aws/awsguru/aws-lambda-adapter:0.9.1 /lambda-adapter /opt/extensions/lambda-adapter

ENV AWS_LWA_INVOKE_MODE=response_stream \
    AWS_LWA_PORT=8000 \
    AWS_LWA_ASYNC_INIT=true \
    PORT=8000 \
    PYTHONUNBUFFERED=1

WORKDIR /var/task

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Glob rather than naming modules individually: an explicit list silently drops
# newly added modules from the image, and the failure only shows at runtime.
COPY *.py ./

# The adapter needs a real HTTP server, so bypass the Lambda runtime bootstrap
# and start uvicorn directly.
ENTRYPOINT []
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
