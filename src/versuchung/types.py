#!/usr/bin/python

import os
import csv
import cStringIO 

class Type(object):
    """This is the base type for all input and output parameters"""
    @property
    def name(self):
        return self.__name
    @name.setter
    def name(self, name):
        self.__name = name

    @property
    def value(self):
        """Default accessor for this kind of data"""
        raise NotImplemented

    @property
    def base_directory(self):
        return self.__base_directory
    @base_directory.setter
    def base_directory(self, value):
        self.__base_directory = value

    def propagate_meta_data(self, subname, other, fields = ["base_directory", "tmp_directory"]):
        other.name = self.name + "-" + subname
        for field in fields:
            if field in dir(self) and field in dir(other):
                # Copy the value
                setattr(other, field, getattr(self, field))

class InputParameter:
    def inp_setup_cmdline_parser(self, parser):
        raise NotImplemented
    def inp_extract_cmdline_parser(self, opts, args):
        raise NotImplemented

    def __parser_option(self, option = None):
        if option:
            return self.name + "-" + option
        return self.name

    def inp_parser_add(self, parser, option, default):
        option = self.__parser_option(option)
        parser.add_option('', '--%s' % option,
                          dest = option,
                          help = "(default: %s)" % default,
                          default = default)

    def inp_parser_extract(self, opts, option):
        return getattr(opts, self.__parser_option(option), None)

    def inp_metadata(self):
        return {}



class OutputParameter:
    def outp_setup_output(self):
        raise NotImplemented
    def outp_tear_down_output(self):
        raise NotImplemented


class String(InputParameter, Type):
    """Can be used as: **input parameter**

    A String is the most simple input parameter."""

    def __init__(self, default_value=""):
        self.__value = default_value

    def inp_setup_cmdline_parser(self, parser):
        self.inp_parser_add(parser, None, self.__value)
    def inp_extract_cmdline_parser(self, opts, args):
        self.__value = self.inp_parser_extract(opts, None)

    def inp_metadata(self):
        return {self.name: self.value}

    @property
    def value(self):
        """The value of the string. This is either the default value
        or the parameter given on the command line"""
        return self.__value

class FilesystemObject(InputParameter, OutputParameter, Type):
    def __init__(self, default_name=""):
        self.__object_name = default_name

    def inp_setup_cmdline_parser(self, parser):
        self.inp_parser_add(parser, None, self.__object_name)

    def inp_extract_cmdline_parser(self, opts, args):
        self.__object_name = self.inp_parser_extract(opts, None)

    def inp_metadata(self):
        return {self.name: self.__object_name}

    @property
    def path(self):
        """:return: string -- path to the file/directory"""
        if not hasattr(self, "base_directory"):
            return os.path.abspath(self.__object_name)
        return os.path.join(self.base_directory, self.__object_name)


class File(FilesystemObject):
    """Can be used as: **input parameter** and **output parameter**

    The File type represents the content of a single file. Its
    contents can be read, overwritten and content can be appended. But
    be aware, that the content is just flushed after the experiment is
    over. If you want to do this manually use :meth:`flush`.
    """

    def __init__(self, default_filename=""):
        FilesystemObject.__init__(self, default_filename)
        self.__value = None

    @property
    def value(self):
        """This attribute can be read and written and represent the
        exact content of the specified file"""
        if not self.__value:
            with open(self.path) as fd:
                self.__value = self.after_read(fd.read())
        return self.__value
    @value.setter
    def value(self, value):
        self.__value = value

    def write(self, content, append = False):
        """Similar to :attr:`value`. If append is false :attr:`value`
        is overwritten, otherwise the content is appendend"""
        if append:
            self.value += content
        else:
            self.value = content

    def outp_setup_output(self):
        # Create the file
        with open(self.path, "w+") as fd:
            fd.write("")

    def outp_tear_down_output(self):
        self.flush()

    def flush(self):
        """Flush the cached content of the file to disk"""
        with open(self.path, "w+") as fd:
            v = self.before_write(self.value)
            if v is None:
                v = ""
            fd.write(v)

    def after_read(self, value):
        """To provide filtering of file contents in subclasses, overrwrite this method.
        It is gets the file content as a string and returns the value()"""
        return value
    def before_write(self, value):
        """To provide filtering of file contents in subclasses, overrwrite this method.
        This method gets the value() and returns a string, when the file is written to disk"""
        return value

class Directory(FilesystemObject):
    """Can be used as: **input parameter** and **output parameter**

    Represents the contents of directory. It can also be used with the
    **with**-keyword to change the directory temporarily to this
    directory::

       with directory as dir:
          # Do something with adjusted current working directory
          print os.curdir
    """

    def __init__(self, default_filename=""):
        FilesystemObject.__init__(self, default_filename)
        self.__value = None
        self.__new_files = []

    @property
    def value(self):
        """:return: list -- directories and files in given directory"""
        if not self.__value:
            self.__value = os.listdir(self.path)
        return self.__value

    def outp_setup_output(self):
        os.mkdir(self.path)

    def outp_tear_down_output(self):
        for f in self.__new_files:
            f.outp_tear_down_output()

    def new_file(self, name):
        """Generate a new :class:`~versuchung.types.File` in the
        directory. It will be flushed automatically if the experiment
        is over."""
        f = File(name)
        f.base_directory = self.path
        self.__new_files.append(f)
        return f

    def __enter__(self):
        self.olddir = os.path.abspath(os.curdir)
        os.chdir(self.path)
        return self
    def __exit__(self, *excinfo):
        os.chdir(self.olddir)
