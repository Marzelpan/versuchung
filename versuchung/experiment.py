# This file is part of versuchung.
#
# versuchung is free software: you can redistribute it and/or modify it under the
# terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version.
#
# versuchung is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
# PARTICULAR PURPOSE.  See the GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along with
# versuchung.  If not, see <http://www.gnu.org/licenses/>.

from __future__ import print_function

from optparse import OptionParser
import datetime
import logging
from versuchung.types import InputParameter, OutputParameter, Type
from versuchung.files import Directory
from versuchung.tools import JavascriptStyleDictAccess, setup_logging
import sys
import os.path
import glob
import hashlib
import json
import shutil
import copy
import tempfile
import signal

LambdaType = type(lambda x:x)

class ExperimentError(Exception):
    pass

class Experiment(Type, InputParameter):
    """Can be used as: **input parameter**"""

    version = 1
    """Version of the experiment, defaults to 1. The version is
    included in the metadata **and** used for the metadata hash."""


    i = None
    """Shorthand for :attr:`~.inputs`"""

    inputs = {}
    """In the input dictionary all input parameters are defined. They
    may and will influence the metadata and the metadata hash. Only
    objects which are marked as **input parameters** may be used
    here. The key in this ``dict`` is used as :attr:`~.name` attribute
    and propagated to the parameters. From these input parameters the
    command line interface is created.

    This ``dict`` can not only be used as a dictionary but also a
    object with the dot-notation (this behaviour is known and widely
    used in javascript). And there is i as a shorthand.

    >>> self.inputs["string_parameter"]
    <versuchung.types.String object at 0xb73fabec>
    >>> self.inputs.string_parameter
    <versuchung.types.String object at 0xb73fabec>
    >>> self.i.string_parameter
    <versuchung.types.String object at 0xb73fabec>
    """

    o = None
    """Shorthand for :attr:`~.outputs`"""

    outputs = {}
    """Similar to the :attr:`~.inputs` attribute, in the output
    dictionary all experiment results are defined. Only objects that
    are explicitly marked as **output parameters** can be used
    here.

    When a experiment is used as an input parameter. The results of
    the old experiment can be accessed through this attribute. Of
    course at all points the short hands for inputs and outputs can be
    used. As well as the javascript style access to dictionary members.

    >>> self.inputs["experiment"].outputs["out_file"]
    <versuchung.types.File object at 0xb736220c>
    >>> self.i.experiment.o.out_file
    <versuchung.types.File object at 0xb736220c>
    """

    title = None
    """Title of the experiment, this is normally the classname"""

    name = None
    """The name of the object. This is in execution mode (Experiment
    instance is the executed experiment) the result set name
    (str). When the experiment is used as input parameter it is the
    key-value in the :attr:`~.inputs` dictionary."""

    suspend_on_error = False
    """Suspend the experiment process, if the run() method fails. The
    path of the tmp-directory is printed after suspension"""

    tmp_directory = None

    # Override base_directory from versuchung.types.Type
    base_directory = None

    @property
    def static_experiment(self):
        return self
    @static_experiment.setter
    def static_experiment(self, value):
        pass

    def __init__(self, default_experiment_instance = None, title=None,inputs=None,outputs=None):
        """The constructor of an experiment just fills in the
        necessary attributes but has *no* sideeffects on the outside
        world.

        :param default_experiment_instance: If used as input
              parameter, this is the default result set used. For
              example
              ``"SimpleExperiment-aeb298601cdc582b1b0d8260195f6cfd"``
        :type default_experiment_instance: str.

        """
        Type.__init__(self)
        InputParameter.__init__(self)
        self.title = title or self.__class__.__name__
        self.static_experiment = self
        if inputs is not None:
            self.__class__.inputs = inputs
        if outputs is not None:
            self.__class__.outputs = outputs
        if default_experiment_instance and not os.path.exists(default_experiment_instance):
            default_experiment_instance = None
        self.__reinit__(default_experiment_instance)

    def __reinit__(self, experiment_path):
        self.__experiment_instance = experiment_path

        if experiment_path:
            if "/" in experiment_path:
                self.__experiment_instance = os.path.basename(experiment_path)

            self.base_directory = os.path.join(os.curdir, experiment_path)
            self.base_directory = os.path.realpath(self.base_directory)
            assert os.path.exists(self.base_directory)

            try:
                with open(os.path.join(experiment_path, "metadata"), "r") as fd:
                    metadata = json.load(fd)
            except json.JSONDecodeError:
                if os.getenv("VERSUCHUNG_METADATA_EVAL"):
                    with open(os.path.join(experiment_path, "metadata"), "r") as fd:
                        metadata = eval(fd.read())
                else:
                    raise RuntimeError("metadata is invalid JSON. Set VERSUCHUNG_METADATA_EVAL=1 to load metadata exported via pprint") from None
            self.__metadata = metadata
        else:
            self.base_directory = None
            self.__metadata = None

        # Copy input and output objects
        self.inputs = JavascriptStyleDictAccess(copy.deepcopy(self.__class__.inputs))
        self.i = self.inputs
        self.outputs = JavascriptStyleDictAccess(copy.deepcopy(self.__class__.outputs))
        self.o = self.outputs

        self.subobjects.clear()

        # Sanity checking for input parameters.
        for (name, inp) in self.inputs.items():
            # Lambdas are resolved, when the experiment is really started
            if type(inp) == LambdaType:
                continue
            if not isinstance(inp, InputParameter):
                print("%s cannot be used as an input parameter" % name)
                sys.exit(-1)
            self.subobjects[name] = inp


        for (name, outp) in self.outputs.items():
            if not isinstance(outp, OutputParameter):
                print("%s cannot be used as an output parameter" % name)
                sys.exit(-1)
            self.subobjects[name] = outp


        # Reinit Children Attributes
        if experiment_path:
            for (name, inp) in self.inputs.items():
                if hasattr(inp, "__reinit__"):
                    try:
                        inp.__reinit__(metadata[name])
                    except:
                        logging.debug('Cannot reinit field %s. Setting it to None', name)
                        self.inputs[name] = None
                else:
                    # We cannot reinit this input from metadata. Therefore it is better to clear it.
                    logging.debug('Cannot reinit field %s. Setting it to None', name)
                    self.inputs[name] = None



    def __setup_parser(self):
        self.__parser = OptionParser("%prog <options>")
        self.__parser.add_option('-d', '--base-dir', dest='base_dir', action='store',
                                 help="Directory which is used for storing the experiment data",
                                 default = ".")
        self.__parser.add_option('--dummy', dest='dummy_result', action='store_true',
                                 help="Use dummy result directory",
                                 default=False)
        self.__parser.add_option('-s', '--symlink', dest='do_symlink', action='store_true',
                                 help="symlink the result dir (as newest)")
        self.__parser.add_option('-v', '--verbose', dest='verbose', action='count', default=0,
                                 help="increase verbosity (specify multiple times for more)")
        self.__parser.add_option('--title', dest='title',
                                 help="custom title of the experiment (default: Experiment class-name)")

        for (name, inp) in self.inputs.items():
            if type(inp) == LambdaType:
                continue
            inp.inp_setup_cmdline_parser(self.__parser)


    def execute(self, args = [], **kwargs):
        """Calling this method will execute the experiment

        :param args: The command line arguments, normally ``sys.argv``
        :type args: list.

        :kwargs: The keyword arguments can be used to overwrite the
          default values of the experiment, without assembling a command
          line.

        The normal mode of operation is to give ``sys.argv`` as
        argument:

        >>> experiment.execute(sys.argv)

        But with keyword arguments the following two expression result
        in the same result set:

        >>> experiment.execute(["--input_parameter", "foo"])
        >>> experiment.execute(input_parameter="foo")
        """
        self.execute_setup(args, **kwargs)
        self.execute_run()
        return self.execute_teardown()

    def execute_setup(self, args=[], **kwargs):
        self.dynamic_experiment = self
        self.startup_directory = os.path.abspath(os.curdir)

        self.subobjects.update()

        # Set up the argument parsing
        self.__setup_parser()
        (opts, args) = self.__parser.parse_args(args)
        setup_logging(opts.verbose)

        self.__opts = opts
        self.__args = args

        for key in kwargs:
            if not hasattr(opts, key):
                raise AttributeError("No argument called %s" % key)
            setattr(opts, key, kwargs[key])

        # Set up the experiment
        self.before_experiment_run("output")

    def execute_run(self):
        # Goto the output directory
        os.chdir(self.base_directory)
        try:
            self.run()
        except RuntimeError as e:
            # Clean up the tmp directory
            if self.suspend_on_error:
                print(str(e))
                print("tmp-dir: %s" % self.tmp_directory.path)
                self.suspend_python()
            logging.error("Removing tmp directory")
            shutil.rmtree(self.tmp_directory.path)
            raise e
        finally:
            os.chdir(self.startup_directory)


    def execute_teardown(self):
        self.after_experiment_run("output")
        return self.__experiment_instance

    @property
    def experiment_identifier(self):
        """:return: string -- directory name of the produced experiment results"""
        return self.__experiment_instance

    __call__ = execute
    """A experiment can also executed by calling it, :attr:`execute`
    will be called.

    >>> experiment(sys.argv)"""

    def suspend_python(self):
        """Suspend the running python process. Give the control back
        to the terminal. This sends a SIGSTOP to the python process"""
        os.kill(os.getpid(), signal.SIGSTOP)


    def before_experiment_run(self, parameter_type):
        # When experiment run as input, just run the normal input handlers
        if parameter_type == "input":
            Type.before_experiment_run(self, "input")
            return

        for (name, inp) in self.inputs.items():
            if type(inp) == LambdaType:
                continue
            ret = inp.inp_extract_cmdline_parser(self.__opts, self.__args)
            if ret:
                (self.__opts, self.__args) = ret

        # After all input parameters are parsed. Execute the
        # calculated input parameters
        for (name, inp) in self.inputs.items():
            if type(inp) != LambdaType:
                continue
            inp = inp(self)
            inp.name = name
            self.subobjects[name] = inp
            self.inputs[name] = inp

        self.subobjects.update()

        # Now set up the experiment tmp directory
        self.tmp_directory = Directory(tempfile.mkdtemp())
        self.subobjects["tmp_directory"] = self.tmp_directory

        for obj in self.inputs.values():
            obj.before_experiment_run("input")

        self.__calculate_metadata()

        for obj in self.outputs.values():
            obj.before_experiment_run("output")


    def __calculate_metadata(self):
        metadata = {}
        for name in self.inputs:
            metadata.update( self.inputs[name].inp_metadata() )
        m = hashlib.md5()
        m.update(("version %s" % str(self.version)).encode())
        calc_metadata = self.filter_metadata(metadata)
        for key in sorted(calc_metadata.keys()):
            m.update((key + " " + str(calc_metadata[key])).encode())

        self.__experiment_instance = "%s-%s" %(self.title, m.hexdigest())
        if self.__opts.dummy_result:
            base = self.tmp_directory.path
        else:
            base = self.__opts.base_dir
        self.base_directory = os.path.join(base, self.__experiment_instance)
        self.base_directory = os.path.realpath(self.base_directory)


        if os.path.exists(self.base_directory):
            logging.info("Removing all files from existing output directory")
            for f in glob.glob(os.path.join(self.base_directory, '*')):
                if os.path.isdir(f):
                    shutil.rmtree(f)
                else:
                    os.unlink(f)

        try:
            os.mkdir(self.base_directory)
        except OSError:
            pass

        # Here the hash is already calculated, so we can change the
        # metadata nonconsitent
        metadata["date-start"] = str(datetime.datetime.now())
        metadata["experiment-name"] = self.title
        metadata["experiment-version"] = self.version
        metadata["experiment-hash"]    = m.hexdigest()

        with open(os.path.join(self.base_directory, "metadata"), "w") as fd:
            json.dump(metadata, fd)

        self.__metadata = metadata


    def symlink_name(self):
        """If -s is given, this function returns the name of the symlink
           object that is created in the base directory.

        """
        return self.title

    def after_experiment_run(self, parameter_type):

        if parameter_type == "output":
            for (name, outp) in self.outputs.items():
                outp.after_experiment_run("output")

            for (name, inp) in self.inputs.items():
                inp.after_experiment_run("input")

            self.__metadata["date-end"] = str(datetime.datetime.now())
            with open(os.path.join(self.base_directory, "metadata"), "w") as fd:
                json.dump(self.__metadata, fd)

            shutil.rmtree(self.tmp_directory.path)

            # Create a Symlink to the newsest result set, if it is not
            # already removed (--dummy)
            if self.__opts.do_symlink and os.path.exists(self.base_directory):
                link = os.path.join(self.base_directory,
                                    "..",
                                    self.symlink_name())
                link = os.path.abspath(link)
                if os.path.islink(link):
                    os.unlink(link)

                if not os.path.exists(link):
                    os.symlink(self.__experiment_instance, link)
                else:
                    logging.warn("Didn't create symlink, %s exists and is no symlink", link)

        else:
            for (name, outp) in self.outputs.items():
                outp.after_experiment_run("input")

    ### Input Type
    def inp_setup_cmdline_parser(self, parser):
        self.inp_parser_add(parser, None, self.__experiment_instance)

    def inp_extract_cmdline_parser(self, opts, args):
        self.__experiment_instance = self.inp_parser_extract(opts, None)
        if not self.__experiment_instance:
            print("Missing argument for %s" % self.title)
            raise ExperimentError

        # Resolve symlink relative to the current directory
        path = os.path.realpath(self.__experiment_instance)
        path = os.path.abspath(path)
        self.__experiment_instance = os.path.basename(path)

        self.base_directory = path
        assert os.path.exists(self.base_directory), \
            "Base Directory does not exist"

        self.__reinit__(path)

        for (name, outp) in self.outputs.items():
            del self.subobjects[name]
            self.subobjects[name] = outp


    def inp_metadata(self):
        return {self.name: self.__experiment_instance}

    @property
    def metadata(self):
        """Return the metadata as python dict. This works for
        experiments, which are running at the moment, and for already
        finished experiments by reading the /metadata file."""
        if not self.__metadata:
            md_path = os.path.join(self.base_directory, "metadata")
            with open(md_path) as fd:
                self.__metadata = json.load(fd)
        return self.__metadata

    @property
    def path(self):
        """Return the path to output directory"""
        return self.base_directory

    def run(self):
        """This method is the heart of every experiment and must be
        implemented by the user. It is called when the experiment is
        executed. Before all input parameters are parsed, the output
        directory is set up. Afterwards all temporary data is removed
        and the output parameters are deinitialized.

        .. warning:: Must be implemented by the user."""
        raise NotImplemented

    def filter_metadata(self, metadata):
        """This method is invocated on the dict which is stored in
        $result_dir/metadata before the result_hash is
        calculated. This helps to take influence on the input
        parameters which alter the experiment hash. So use it with care.

        .. note:: Can be implemented by the user."""

        return metadata

    def __getattribute__(self, name):
         try:
             return object.__getattribute__(self, name)
         except AttributeError:
             pass
         inp = None
         outp = None
         try:
             inp = getattr(self.inputs, name)
         except AttributeError:
             pass
         try:
             outp = getattr(self.outputs, name)
         except AttributeError:
             pass

         if inp != None and outp != None:
             raise AttributeError("'%s.%s' is ambigous, use .inputs/.outputs" %(\
                 self.__class__.__name__,
                 name))
         elif inp != None:
             return inp
         elif outp != None:
             return outp

         raise AttributeError("'%s' object has no attribute '%s'" %(\
             self.__class__.__name__,
             name))
