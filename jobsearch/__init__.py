"""JobSearch — pipeline automatisé de recherche d'emploi.

`__version__` est la SOURCE DE VÉRITÉ de la version, et `pyproject.toml` la lit
dynamiquement (`[tool.setuptools.dynamic]`). L'inverse — lire
`importlib.metadata.version("jobsearch")` — est un piège : en installation
éditable (`pip install -e .`, `pipx install <chemin>`), les métadonnées de
distribution sont figées au moment de l'installation et ne suivent pas les
changements de version du code. Mesuré sur l'installation de référence : le
paquet déclarait 1.2.0 pendant que `importlib.metadata` répondait encore 0.1.0.
Une vérification de mise à jour fondée là-dessus annoncerait une version en
retard même juste après une mise à jour.
"""

__version__ = "1.3.0"
