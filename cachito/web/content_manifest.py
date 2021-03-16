# SPDX-License-Identifier: GPL-3.0-or-later
import os.path
from typing import Optional

import flask

from cachito.web.utils import deep_sort_icm
from cachito.workers.pkg_managers import gomod


class ContentManifest:
    """A content manifest associated with a Cacihto request."""

    version = 1
    json_schema_url = (
        "https://raw.githubusercontent.com/containerbuildsystem/atomic-reactor/"
        "f4abcfdaf8247a6b074f94fa84f3846f82d781c6/atomic_reactor/schemas/content_manifest.json"
    )
    unknown_layer_index = -1

    def __init__(self, request):
        """
        Initialize ContentManifest.

        :param Request request: the request to generate a ContentManifest for
        """
        self.request = request
        # dict to store go package level data; uses the package id as key to identify a package
        self._gopkg_data = {}
        # dict to store go module level purl dependencies. Module names are used as keys
        self._gomod_data = {}
        # dict to store npm package data; uses the package id as key to identify a package
        self._npm_data = {}
        # dict to store pip package data; uses the package id as key to identify a package
        self._pip_data = {}
        # dict to store yarn package data; uses the package id as key to identify a package
        self._yarn_data = {}
        # dict to store gitsubmodule package level data; uses the package id as key to identify a
        # package
        self._gitsubmodule_data = {}

        # cache for matching go packages to their parent modules
        self._gopkg_to_parent_gomod = {}

    def process_gomod(self, package, dependency):
        """
        Process gomod package.

        :param Package package: the gomod package to process
        :param Dependency dependency: the gomod package dependency to process
        """
        if dependency.type == "gomod":
            if dependency.version.startswith("."):
                parent_purl = self._gomod_data[package.name]["purl"]
                dep_purl = _add_subpath(parent_purl, dependency.version)
            else:
                dep_purl = dependency.to_purl()

            icm_source = {"purl": dep_purl}
            self._gomod_data[package.name]["dependencies"].append(icm_source)

    def process_go_package(self, package, dependency):
        """
        Process go-package package.

        :param Package package: the go-package package to process
        :param Dependency dependency: the go-package package dependency to process
        """
        if dependency.type == "go-package":
            module_name = self._get_parent_go_module(package.name)
            module_purl = self._gomod_data[module_name]["purl"] if module_name else None

            if module_purl and dependency.version.startswith("."):
                dep_purl = _add_subpath(module_purl, dependency.version)
            elif (
                module_purl
                and not module_purl.startswith("pkg:golang")
                and dependency.name.startswith(module_name)
            ):
                # If the parent module uses a vcs purl and the dependency belongs to the module,
                # also use a vcs purl for the dependency
                subpath = gomod.path_to_subpackage(module_name, dependency.name)
                dep_purl = _add_subpath(module_purl, subpath)
            else:
                dep_purl = dependency.to_purl()

            icm_dependency = {"purl": dep_purl}
            self._gopkg_data[package.id]["dependencies"].append(icm_dependency)

    def set_go_package_sources(self):
        """
        Adjust source level dependencies for go packages.

        Go packages are not related to Go modules in cachito's DB. However, Go
        sources are retreived in a per module basis. To set the proper source
        in each content manifest entry, we associate each Go package to a Go
        module based on their names.
        """
        for package_id, pkg_data in self._gopkg_data.items():
            pkg_name = pkg_data.pop("name")
            module_name = self._get_parent_go_module(pkg_name)

            if module_name is not None:
                module = self._gomod_data[module_name]
                self._gopkg_data[package_id]["sources"] = module["dependencies"]

    def _get_parent_go_module(self, go_pkg_name: str) -> Optional[str]:
        if go_pkg_name in self._gopkg_to_parent_gomod:
            return self._gopkg_to_parent_gomod[go_pkg_name]

        if go_pkg_name in self._gomod_data:
            module_name = go_pkg_name
        else:
            module_name = gomod.match_parent_module(go_pkg_name, self._gomod_data.keys())

        if module_name is None:
            flask.current_app.logger.warning("Could not find a Go module for %s", go_pkg_name)

        self._gopkg_to_parent_gomod[go_pkg_name] = module_name
        return module_name

    def process_npm_package(self, package, dependency):
        """
        Process npm package.

        :param Package package: the npm package to process
        :param Dependency dependency: the npm package dependency to process
        """
        if dependency.type == "npm":
            self._process_standard_package("npm", package, dependency)

    def process_pip_package(self, package, dependency):
        """
        Process pip package.

        :param Package package: the pip package to process
        :param Dependency dependency: the pip package dependency to process
        """
        if dependency.type == "pip":
            self._process_standard_package("pip", package, dependency)

    def process_yarn_package(self, package, dependency):
        """
        Process yarn package.

        :param Package package: the yarn package to process
        :param Dependency dependency: the yarn package dependency to process
        """
        if dependency.type == "yarn":
            self._process_standard_package("yarn", package, dependency)

    def _process_standard_package(self, pkg_type, package, dependency):
        """
        Process a standard package (standard = does not require the same magic as go packages).

        Currently, all package types except for gomod and go-package are standard.
        """
        pkg_type_data = getattr(self, f"_{pkg_type}_data")

        icm_dependency = {"purl": dependency.to_purl()}
        pkg_type_data[package.id]["sources"].append(icm_dependency)
        if not dependency.dev:
            pkg_type_data[package.id]["dependencies"].append(icm_dependency)

    def to_json(self):
        """
        Generate the JSON representation of the content manifest.

        :return: the JSON form of the ContentManifest object
        :rtype: OrderedDict
        """
        self._gopkg_data = {}
        self._gomod_data = {}
        self._npm_data = {}
        self._pip_data = {}
        self._yarn_data = {}
        self._gitsubmodule_data = {}

        # Address the possibility of packages having no dependencies
        for request_package in self.request.request_packages:
            package = request_package.package

            if package.type == "go-package":
                purl = package.to_top_level_purl(self.request, subpath=request_package.subpath)
                self._gopkg_data.setdefault(
                    package.id,
                    {"name": package.name, "purl": purl, "dependencies": [], "sources": []},
                )
            elif package.type == "gomod":
                purl = package.to_top_level_purl(self.request, subpath=request_package.subpath)
                self._gomod_data.setdefault(package.name, {"purl": purl, "dependencies": []})
            elif package.type in ("npm", "pip", "yarn"):
                purl = package.to_top_level_purl(self.request, subpath=request_package.subpath)
                data = getattr(self, f"_{package.type}_data")
                data.setdefault(package.id, {"purl": purl, "dependencies": [], "sources": []})
            elif package.type == "git-submodule":
                purl = package.to_top_level_purl(self.request, subpath=request_package.subpath)
                self._gitsubmodule_data.setdefault(
                    package.id, {"purl": purl, "dependencies": [], "sources": []}
                )
            else:
                flask.current_app.logger.debug(
                    "No ICM implementation for '%s' packages", package.type
                )

        for req_dep in self.request.request_dependencies:
            if req_dep.package.type == "go-package":
                self.process_go_package(req_dep.package, req_dep.dependency)
            elif req_dep.package.type == "gomod":
                self.process_gomod(req_dep.package, req_dep.dependency)
            elif req_dep.package.type == "npm":
                self.process_npm_package(req_dep.package, req_dep.dependency)
            elif req_dep.package.type == "pip":
                self.process_pip_package(req_dep.package, req_dep.dependency)
            elif req_dep.package.type == "yarn":
                self.process_yarn_package(req_dep.package, req_dep.dependency)

        # Adjust source level dependencies for go packages
        self.set_go_package_sources()

        top_level_packages = [
            *self._gopkg_data.values(),
            *self._npm_data.values(),
            *self._pip_data.values(),
            *self._yarn_data.values(),
            *self._gitsubmodule_data.values(),
        ]
        return self.generate_icm(top_level_packages)

    def generate_icm(self, image_contents=None):
        """
        Generate a content manifest with the given image contents.

        :param list image_contents: List with components for the ICM's ``image_contents`` field
        :return: a valid Image Content Manifest
        :rtype: OrderedDict
        """
        icm = {
            "metadata": {
                "icm_version": self.version,
                "icm_spec": self.json_schema_url,
                "image_layer_index": self.unknown_layer_index,
            },
        }
        icm["image_contents"] = image_contents or []

        return deep_sort_icm(icm)


def _add_subpath(purl: str, subpath: str) -> str:
    normpath = os.path.normpath(subpath)
    if "#" in purl:
        return f"{purl}/{normpath}"
    else:
        return f"{purl}#{normpath}"
