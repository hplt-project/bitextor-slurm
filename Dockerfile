#Download base image ubuntu (latest version)
FROM ubuntu:22.04

ARG DEBIAN_FRONTEND=noninteractive
ARG j=32

# Update Software repository
ENV RED '\033[0;31m'
ENV NC '\033[0m'
RUN echo "Updating Software repository"
RUN apt-get update && apt-get upgrade -y && apt-get autoremove -y

# Pretend that HOME is /home/docker because having things in / is awkward
RUN mkdir -p /home/docker
ENV HOME=/home/docker

# Add required dependencies
RUN echo "Installing core apt dependencies"
RUN apt-get -y install git cmake python-is-python3 python3 python3-venv python3-pip \
    libboost-thread-dev libboost-regex-dev libboost-filesystem-dev \
    libboost-log-dev libboost-iostreams-dev libboost-locale-dev \
    libboost-program-options-dev libboost-serialization-dev \
    curl wget pigz unzip time parallel bc
# warc2text
RUN echo "Installing warc2text apt dependencies"
RUN apt-get -y install libuchardet-dev libzip-dev
# pdf-extract
RUN echo "Installing pdf-extract apt dependencies"
RUN apt-get -y install openjdk-8-jdk
# biroamer
RUN echo "Installing biroamer apt dependencies"
RUN apt-get -y install libgoogle-perftools-dev libsparsehash-dev
# fastspell
RUN echo "Installing fastspell apt dependencies"
RUN apt-get -y install autopoint autoconf automake libtool # hunspell-af hunspell-bg hunspell-bs hunspell-ca hunspell-cs hunspell-da hunspell-es hunspell-gl hunspell-hr hunspell-nl hunspell-no hunspell-pt-pt hunspell-sk hunspell-sl hunspell-sr


# random utilities:
# not necessary for bitextor, but users might find this useful:
RUN apt-get -y install htop vim

# Support for UTF8
# RUN locale-gen en_US.UTF-8
# ENV LANG=en_US.UTF-8 LANGUAGE=en_US:en LC_ALL=en_US.UTF-8
RUN apt-get install -y locales
RUN locale-gen en_US.UTF-8
ENV LANG=en_US.UTF-8 LANGUAGE=en_US:en LC_ALL=en_US.UTF-8

# Installing protobuf
#RUN echo "Installing protobuf and CLD3"
#WORKDIR /home/docker
#RUN apt-get install -y autoconf automake libtool
#RUN wget https://github.com/protocolbuffers/protobuf/releases/download/v3.19.1/protobuf-all-3.19.1.tar.gz
#RUN tar -zxvf protobuf-all-3.19.1.tar.gz
#RUN rm protobuf-all-3.19.1.tar.gz
#WORKDIR /home/docker/protobuf-3.19.1
#RUN ./configure
#RUN make -j $j
#RUN make install
#RUN ldconfig

# Installing giashard
# RUN echo "Installing golang"
# WORKDIR /home/docker
# RUN wget -O go.tgz https://dl.google.com/go/go1.17.3.linux-amd64.tar.gz
# RUN tar -C /usr/local -xzf go.tgz && rm go.tgz
# ENV PATH "/usr/local/go/bin:$PATH"
# RUN go version
# ENV GOPATH /home/docker/go
# ENV PATH $GOPATH/bin:/usr/local/go/bin:$PATH
# RUN mkdir -p "$GOPATH/src" "$GOPATH/bin" && chmod -R 777 "$GOPATH"
# RUN echo "Installing giashard"
# RUN go install github.com/paracrawl/giashard/cmd/giashard@latest

# # Download Heritrix
# RUN echo "Downloading heritrix"
# WORKDIR /home/docker
# RUN wget https://repo1.maven.org/maven2/org/archive/heritrix/heritrix/3.4.0-20210923/heritrix-3.4.0-20210923-dist.zip
# RUN unzip heritrix-3.4.0-20210923-dist.zip && rm heritrix-3.4.0-20210923-dist.zip

# Cloning bitextor
RUN echo "Cloning bitextor"
RUN git clone --recursive --jobs 6 https://github.com/bitextor/bitextor /home/docker/bitextor
WORKDIR /home/docker/bitextor
RUN git checkout v8.3
#COPY ./ bitextor/

# Installing bitextor dependencies
RUN echo "Installing pip dependencies"
#WORKDIR /home/docker/bitextor
RUN pip3 install --upgrade pip
## bitextor
RUN pip3 install .
## bicleaner
RUN pip3 install git+https://github.com/MSeal/cython_hunspell@2.0.3
#RUN pip3 install ./third_party/bicleaner
RUN pip3 install ./third_party/bicleaner-ai
RUN pip3 install ./third_party/kenlm --config-settings="--build-option=--max_order=7"
RUN fastspell-download
##  bifixer
RUN pip3 install ./third_party/bifixer
## biroamer
# RUN pip3 install ./third_party/biroamer
# RUN python3 -c "from flair.models import SequenceTagger; SequenceTagger.load('flair/ner-english-fast')"
## neural
# RUN pip3 install ./third_party/neural-document-aligner
# RUN pip3 install ./third_party/vecalign
## cld3
RUN pip3 install Cython
# RUN pip3 install pycld3

RUN apt-get -y install libboost-test-dev libbz2-dev libeigen3-dev

# Installing bitextor
RUN echo "Compiling bitextor"
WORKDIR /home/docker/bitextor
RUN mkdir -p build
WORKDIR /home/docker/bitextor/build
RUN cmake -DCMAKE_INSTALL_PREFIX=/usr \
    -DSKIP_MGIZA=on -DSKIP_WARC2TEXT=on \
    -DSKIP_PREVERTICAL2TEXT=on -DSKIP_HUNALIGN=on \
    ..
RUN make -j $j install

RUN git clone https://github.com/hplt-project/bitextor-slurm /opt/bitextor-slurm
WORKDIR /opt/bitextor-slurm
RUN git submodule update --init env/src/preprocess/
RUN mkdir env/src/paracrawl/build && \
    cd env/src/paracrawl/build && \
    cmake .. && \
    make -j8 merge_sort && \
    cp bin/merge_sort /usr/local/bin/
WORKDIR /

COPY env/src/GPG-PUB-KEY-INTEL-SW-PRODUCTS.PUB /mkl-key.pub
RUN mkdir -p /etc/apt/keyrings
RUN gpg --dearmor -o /etc/apt/keyrings/mkl.gpg /mkl-key.pub && rm /mkl-key.pub
RUN echo "deb [signed-by=/etc/apt/keyrings/mkl.gpg] https://apt.repos.intel.com/mkl all main" > /etc/apt/sources.list.d/intel-mkl.list
RUN apt-get update && apt-get install -yy intel-mkl-64bit-2020.0-088

# Compile Marian CPU from Bergamot
RUN git clone https://github.com/browsermt/marian-dev /opt/marian-bergamot
WORKDIR /opt/marian-bergamot
RUN git checkout 2be8344fcf2776fb43a7376284067164674cbfaf
WORKDIR /opt/marian-bergamot/build
RUN cmake .. -DUSE_SENTENCEPIECE=on -DCOMPILE_CUDA=off -DUSE_FBGEMM=on
RUN make -j24

RUN pip install -U bicleaner-ai
RUN pip uninstall -y tensorflow keras
RUN pip install tensorflow-rocm==2.12.1.600
RUN fastspell-download

RUN apt-get remove -yy intel-mkl-64bit-2020.0-088 build-essential && apt-get -yy autoremove && \
    rm -Rf /opt/marian-bergamot/build/src && \
    rm -Rf /opt/marian-bergamot/src && \
    rm -Rf /opt/marian-bergamot/build/local && \
    rm -Rf /opt/marian-bergamot/build/libmarian.a && \
    strip /opt/marian-bergamot/build/marian* && \
    strip /opt/marian-bergamot/build/spm*

RUN apt install -y libnuma-dev
