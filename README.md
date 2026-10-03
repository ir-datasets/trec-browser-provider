# A Provider that integrates the Trec Browser into ir-datasets

This allows to use runs and or evaluations from the [TREC Browser](https://pages.nist.gov/trec-browser/) in ir_datasets.

You need a user name and a password to access those files (please ask people from the TREC community or in the SIGIR slack if you need credentials).

Configure the credentials via environment variables:
```
export IRDS_TREC_BROWSER_USERNAME=...
export IRDS_TREC_BROWSER_PASSWORD=...
```

With those environment variables, you can access the run files and evaluations via ir_datasets, e.g.,:

```
ir_datasets export 'trec-browser:trec28/decision/input.ICTNETv1BM25.gz' scoreddocs
```

If you use run files, or data, please ensure to cite the corresponding paper of the run file and please also cite the TREC Browser.

```
@inproceedings{DBLP:conf/sigir/0002VS24,
  author       = {Timo Breuer and Ellen M. Voorhees and Ian Soboroff},
  editor       = {Grace Hui Yang and Hongning Wang and Sam Han and Claudia Hauff and Guido Zuccon and Yi Zhang},
  title        = {Browsing and Searching Metadata of {TREC}},
  booktitle    = {Proceedings of the 47th International {ACM} {SIGIR} Conference on Research and Development in Information Retrieval, {SIGIR} 2024, Washington DC, USA, July 14-18, 2024},
  pages        = {313--323},
  publisher    = {{ACM}},
  year         = {2024},
  url          = {https://doi.org/10.1145/3626772.3657873},
  doi          = {10.1145/3626772.3657873},
  timestamp    = {Sun, 19 Jan 2025 13:11:21 +0100},
  biburl       = {https://dblp.org/rec/conf/sigir/0002VS24.bib},
  bibsource    = {dblp computer science bibliography, https://dblp.org}
}

```
