FROM ubuntu:latest

ENV DEBIAN_FRONTEND=noninteractive

WORKDIR /evopipe

COPY requirements.txt requirements.txt
COPY pipe.py pipe.py

RUN apt-get update && apt-get install -y curl
RUN bash -c "$(curl -fsSL https://raw.githubusercontent.com/moozeq/GP_EvoTrees/pipeline/setup.sh)"

ENV PATH=/miniconda/bin:$PATH

ENTRYPOINT ["/miniconda/bin/python3", "pipe.py"]
