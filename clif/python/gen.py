# Copyright 2017 Google Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Generator helpers.

Produces pieces of generated code.
"""

from clif.python import astutils
from clif.python import postconv

VERSION = '0.2'   # CLIF generated API version. Pure informative.
PY3OUTPUT = None  # Target Python3 on True, Py2 on False, None-don't care.
I = '  '


def WriteTo(channel, lines):
  for s in lines:
    channel.write(s)
    channel.write('\n')


def Headlines(src_file, hdr_files=(), sys_hdr_files=(), open_ns=None):
  """Generate header comment and #includes.

  Args:
    src_file: str - full name of the source file (C++ header)
    hdr_files: [str] - additional c++ headers to #include "str"
      If the first name is PYTHON, #include <Python.h>.
      If str == PYOBJ, forward declare PyObject.
    sys_hdr_files: set(str) - additional c++ headers to #include <str>
    open_ns: str - emit namespace open_ns if not empty.

  Yields:
    source code lines
  """
  yield '/' * 70
  yield ('// This file was automatically generated by CLIF'
         + ('' if PY3OUTPUT is None else
            ' to run under Python %d' % (3 if PY3OUTPUT else 2)))
  yield '// Version %s' % VERSION
  yield '/' * 70
  if src_file:
    yield '// source: %s' % src_file
  yield ''
  python_h = False
  if hdr_files[:1] == ['PYTHON']:
    python_h = True
    yield '#include <Python.h>'
    del hdr_files[0]
  for h in sys_hdr_files:
    if h:
      yield '#include <%s>' % h
  for h in hdr_files:
    if h == 'PYOBJ' and not python_h:
      yield ''
      yield '// Forward "declare" PyObject (instead of #include <Python.h>)'
      yield 'struct _object; typedef _object PyObject;'
    elif h:
      yield '#include "%s"' % h
  if open_ns:
    yield ''
    yield OpenNs(open_ns)


def OpenNs(namespace):
  namespace = (namespace or 'clif').strip(':')
  return ' '.join('namespace %s {' % ns for ns in namespace.split('::'))


def CloseNs(namespace):
  namespace = (namespace or 'clif').strip(':')
  return '} '*(1+namespace.count('::'))+' // namespace '+namespace


def TypeConverters(type_namespace, types, *gen_cvt_args):
  """Generate type converters for types in type_namespace."""
  type_namespace = type_namespace or 'clif'
  yield ''
  yield OpenNs(type_namespace)
  if type_namespace != 'clif':
    yield 'using namespace ::clif;'
    yield 'using ::clif::Clif_PyObjAs;'
    yield 'using ::clif::Clif_PyObjFrom;'
  for t in types:
    for s in (
        t.GenConverters(*gen_cvt_args)
        ): yield s
  yield ''
  yield CloseNs(type_namespace)


def _DefLine(pyname, cname, meth, doc):
  if 'KEYWORD' in meth or 'NOARGS' in meth:
    cname = '(PyCFunction)'+cname
  return '{C("%s"), %s, %s, C("%s")}' % (pyname, cname, meth, doc)


def _DefTable(ctype, cname, lines):
  yield ''
  yield 'static %s %s[] = {' % (ctype, cname)
  for p in lines:
    yield I+_DefLine(*p)+','
  yield I+'{}'
  yield '};'


def MethodDef(methods):
  for s in (
      _DefTable('PyMethodDef', 'Methods', methods)
      ): yield s
MethodDef.name = 'Methods'


def GetSetDef(properties):
  for s in (
      _DefTable('PyGetSetDef', 'Properties', properties)
      ): yield s
GetSetDef.name = 'Properties'


def ReadyFunction(types_init):
  """Generate Ready() function to call PyType_Ready for wrapped types."""
  yield ''
  yield 'bool Ready() {'
  for cppname, base, _ in types_init:
    if base:
      if '.' in base:
        # |base| is a fully qualified Python name.
        # The caller ensures we have only one Python base.
        yield I+'PyObject* base_cls = ImportFQName("%s");' % base
        yield I+'if (base_cls == nullptr) return false;'
        yield I+'if (!PyObject_TypeCheck(base_cls, &PyType_Type)) {'
        yield I+I+'Py_DECREF(base_cls);'
        yield I+I+('PyErr_SetString(PyExc_TypeError, "Base class %s is not a '
                   'new style class inheriting from object.");' % base)
        yield I+I+'return false;'
        yield I+'}'
        yield I+('%s.tp_base = reinterpret_cast<PyTypeObject*>(base_cls);'
                 % cppname)
        yield I+'// Check that base_cls is a *statically* allocated PyType.'
        yield I+'if (%s.tp_base->tp_alloc == PyType_GenericAlloc) {' % cppname
        yield I+I+'Py_DECREF(base_cls);'
        yield I+I+('PyErr_SetString(PyExc_TypeError, "Base class %s is a'
                   ' dynamic (Python defined) class.");' % base)
        yield I+I+'return false;'
        yield I+'}'
      else:
        # base is Python wrapper type in a C++ class namespace defined locally.
        # Allow to inherit only from top-level classes.
        yield I+'%s.tp_base = &%s;' % (cppname, base)
    yield I+'if (PyType_Ready(&%s) < 0) return false;' % cppname
    yield I+'Py_INCREF(&%s);  // For PyModule_AddObject to steal.' % cppname
  yield I+'return true;'
  yield '}'


def InitFunction(pathname, doc, meth_ref, init, dict_):
  """Generate a function to create the module and initialize it."""
  if PY3OUTPUT:
    yield ''
    yield 'static struct PyModuleDef Module = {'
    yield I+'PyModuleDef_HEAD_INIT,'
    yield I+'"%s",  // module name' % pathname
    yield I+'"%s", // module doc' % doc
    yield I+'-1,  // module keeps state in global variables'
    yield I+meth_ref
    yield '};'
  yield ''
  yield 'PyObject* Init() {'
  if PY3OUTPUT:
    yield I+'PyObject* module = PyModule_Create(&Module);'
  else:
    yield I+'PyObject* module = Py_InitModule3("%s", %s, "%s");' % (
        pathname, meth_ref, doc)
  yield I+'if (!module) return nullptr;'
  init_needs_err = False
  for s in init:
    assert ' return' not in s, 'use "goto err;" to handle errors'
    if ' err;' in s: init_needs_err = True
    yield I+s
  for pair in dict_:
    yield I+'if (PyModule_AddObject(module, "%s", %s) < 0) goto err;' % pair
  yield I+'return module;'
  if init_needs_err or dict_:
    yield 'err:'
    if PY3OUTPUT:
      yield I+'Py_DECREF(module);'
    yield I+'return nullptr;'
  yield '}'


def TypeObject(tp_slots, slotgen, pyname, wname, fqclassname, ctor,
               abstract, async_dtor=False, subst_cpp_ptr=''):
  """Generate PyTypeObject methods and table.

  Args:
    tp_slots: dict - values for PyTypeObject slots
    slotgen: generator to produce body of PyTypeObject using tp_slots
    pyname: str - Python class name
    wname: str - C++ wrapper class name
    fqclassname: str - FQ C++ class (being wrapped) name
    ctor: str - (WRAPped/DEFault/None) type of generated ctor
    abstract: bool - wrapped C++ class is abstract
    async_dtor: bool - allow Python threads during C++ destructor
    subst_cpp_ptr: str - C++ "replacement" class (being wrapped) if any

  Yields:
     Source code for PyTypeObject and tp_alloc / tp_init / tp_free methods.
  """
  yield ''
  yield '// %s __new__' % pyname
  yield 'static PyObject* _allocator(PyTypeObject* type, Py_ssize_t nitems);'
  yield '// %s __init__' % pyname
  yield 'static int _ctor(PyObject* self, PyObject* args, PyObject* kw);'
  yield ''
  yield 'static void _dtor(void* self) {'
  if async_dtor:
    yield I+'Py_BEGIN_ALLOW_THREADS'
  yield I+'delete reinterpret_cast<%s*>(self);' % wname
  if async_dtor:
    yield I+'Py_END_ALLOW_THREADS'
  yield '}'
  tp_slots['tp_free'] = '_dtor'
  tp_slots['tp_dealloc'] = 'Clif_PyType_GenericFree'
  tp_slots['tp_alloc'] = '_allocator'
  tp_slots['tp_new'] = 'PyType_GenericNew'
  tp_slots['tp_init'] = '_ctor' if ctor else 'Clif_PyType_Inconstructible'
  tp_slots['tp_basicsize'] = 'sizeof(%s)' % wname
  tp_slots['tp_itemsize'] = tp_slots['tp_version_tag'] = '0'
  tp_slots['tp_dictoffset'] = tp_slots['tp_weaklistoffset'] = '0'
  tp_slots['tp_flags'] = ' | '.join(tp_slots['tp_flags'])
  tp_slots['tp_doc'] = '"CLIF wrapper for %s"' % fqclassname
  wtype = '%s_Type' % wname
  yield ''
  yield 'PyTypeObject %s = {' % wtype
  yield I+'PyVarObject_HEAD_INIT(&PyType_Type, 0)'
  for s in slotgen(tp_slots):
    yield s
  yield '};'
  yield ''
  if ctor:
    yield 'static int _ctor(PyObject* self, PyObject* args, PyObject* kw) {'
    if abstract:
      yield I+'if (Py_TYPE(self) == &%s) {' % wtype
      yield I+I+'return Clif_PyType_Inconstructible(self, args, kw);'
      yield I+'}'
    if ctor == 'DEF':
      # Skip __init__ if it's a METH_NOARGS.
      yield I+('if ((args && PyTuple_GET_SIZE(args) != 0) ||'
               ' (kw && PyDict_Size(kw) != 0)) {')
      yield I+I+('PyErr_SetString(PyExc_TypeError, "%s takes no arguments");' %
                 pyname)
      yield I+I+'return -1;'
      yield I+'}'
      cpp = 'reinterpret_cast<%s*>(self)->cpp' % wname
      yield I+'%s = ::clif::MakeShared<%s>();' % (cpp,
                                                  subst_cpp_ptr or fqclassname)
      if subst_cpp_ptr:
        yield I+'%s->::clif::PyObj::Init(self);' % cpp
      yield I+'return 0;'
    else:  # ctor is WRAP (holds 'wrapper name')
      yield I+'PyObject* init = %s(self, args, kw);' % ctor
      yield I+'Py_XDECREF(init);'
      yield I+'return init? 0: -1;'
    yield '}'
  yield ''
  yield 'static PyObject* _allocator(PyTypeObject* type, Py_ssize_t nitems) {'
  yield I+'assert(nitems == 0);'
  yield I+'PyObject* self = reinterpret_cast<PyObject*>(new %s);' % wname
  yield I+'return PyObject_Init(self, &%s);' % wtype
  yield '}'


def _CreateInputParameter(func_name, ast_param, arg, args):
  """Return a string to create C++ stack var named arg. args += arg getter."""
  ptype = ast_param.type
  ctype = ptype.cpp_type
  smartptr = (ctype.startswith('::std::unique_ptr') or
              ctype.startswith('::std::shared_ptr'))
  # std::function special case
  if not ctype:
    assert ptype.callable, 'Non-callable param has empty cpp_type'
    if len(ptype.callable.returns) > 1:
      raise ValueError('Callbacks may not have any output parameters, '
                       '%s param %s has %d' % (func_name, ast_param.name.native,
                                               len(ptype.callable.returns)-1))
    args.append('std::move(%s)' % arg)
    return 'std::function<%s> %s;' % (astutils.StdFuncParamStr(ptype.callable),
                                      arg)
  # T*
  if ptype.cpp_raw_pointer:
    if ptype.cpp_toptr_conversion:
      args.append(arg)
      return '%s %s;' % (ctype, arg)
    t = ctype[:-1]
    if ctype.endswith('*'):
      if ptype.cpp_abstract:
        if ptype.cpp_touniqptr_conversion:
          args.append(arg+'.get()')
          return '::std::unique_ptr<%s> %s;' % (t, arg)
      elif ptype.cpp_has_public_dtor:
        # Create a copy on stack and pass its address.
        if ptype.cpp_has_def_ctor:
          args.append('&'+arg)
          return '%s %s;' % (t, arg)
        else:
          args.append('&%s.value()' % arg)
          return '::gtl::optional<%s> %s;' % (t, arg)
    raise TypeError("Can't convert %s to %s" % (ptype.lang_type, ctype))
  if (smartptr or ptype.cpp_abstract) and not ptype.cpp_touniqptr_conversion:
    raise TypeError('Can\'t create "%s" variable (C++ type %s) in function %s'
                    ', no valid conversion defined'
                    % (ast_param.name.native, ctype, func_name))
  # unique_ptr<T>, shared_ptr<T>
  if smartptr:
    args.append('std::move(%s)' % arg)
    return '%s %s;' % (ctype, arg)
  # T, [const] T&
  if ptype.cpp_toptr_conversion:
    args.append('*'+arg)
    return '%s* %s;' % (ctype, arg)
  if ptype.cpp_abstract:  # for AbstractType &
    args.append('*'+arg)
    return 'std::unique_ptr<%s> %s;' % (ctype, arg)
  # Create a copy on stack (even fot T&, most cases should have to_T* conv).
  if ptype.cpp_has_def_ctor:
    args.append('std::move(%s)' % arg)
    return '%s %s;' % (ctype, arg)
  else:
    args.append(arg+'.value()')
    return '::gtl::optional<%s> %s;' % (ctype, arg)


def FunctionCall(pyname, wrapper, doc, catch, call, postcall_init,
                 typepostconversion, func_ast, lineno, prepend_self=None):
  """Generate PyCFunction wrapper from AST.FuncDecl func_ast.

  Args:
    pyname: str - Python function name (may be special: ends with @)
    wrapper: str - generated function name
    doc: str - C++ sinature
    catch: bool - catch C++ exceptions
    call: str | [str] - C++ command(s) to call the wrapped function
      (without "(params);" part).
    postcall_init: str - C++ command; to (re)set ret0.
    typepostconversion: dict(pytype, index) to convert to pytype
    func_ast: AST.FuncDecl protobuf
    lineno: int - .clif line number where func_ast defined
    prepend_self: AST.Param - Use self as 1st parameter.

  Yields:
     Source code for wrapped function.
  """
  ctxmgr = pyname.endswith('@')
  if ctxmgr:
    ctxmgr = pyname
    assert ctxmgr in ('__enter__@', '__exit__@'), (
        'Invalid context manager name ' + pyname)
    pyname = pyname.rstrip('@')
  nret = len(func_ast.returns)
  params = []  # C++ parameter names.
  nargs = len(func_ast.params)
  yield ''
  if func_ast.classmethod:
    yield '// @classmethod ' + doc
    arg0 = 'cls'  # Extra protection that generated code does not use 'self'.
  else:
    yield '// ' + doc
    arg0 = 'self'
  yield 'static PyObject* %s(PyObject* %s%s) {' % (
      wrapper, arg0, ', PyObject* args, PyObject* kw' if nargs else '')
  if prepend_self:
    yield I+_CreateInputParameter(pyname+' line %d' % lineno, prepend_self,
                                  'arg0', params)
    yield I+'if (!Clif_PyObjAs(self, &arg0)) return nullptr;'
  minargs = sum(1 for p in func_ast.params if not p.default_value)
  if nargs:
    yield I+'PyObject* a[%d]%s;' % (nargs, '' if minargs == nargs else '{}')
    yield I+'char* names[] = {'
    for p in func_ast.params:
      yield I+I+I+'C("%s"),' % p.name.native
    yield I+I+I+'nullptr'
    yield I+'};'
    yield I+('if (!PyArg_ParseTupleAndKeywords(args, kw, "%s:%s", names, %s)) '
             'return nullptr;' % ('O'*nargs if minargs == nargs else
                                  'O'*minargs+'|'+'O'*(nargs-minargs), pyname,
                                  ', '.join('&a[%d]'%i for i in range(nargs))))
    if minargs < nargs:
      yield I+'int nargs;  // Find how many args actually passed in.'
      yield I+'for (nargs = %d; nargs > %d; --nargs) {' % (nargs, minargs)
      yield I+I+'if (a[nargs-1] != nullptr) break;'
      yield I+'}'
    # Convert input parameters from Python.
    for i, p in enumerate(func_ast.params):
      n = i+1
      arg = 'arg%d' % n
      yield I+_CreateInputParameter(pyname+' line %d' % lineno, p, arg, params)
      cvt = ('if (!Clif_PyObjAs(a[{i}], &{cvar})) return ArgError'
             '("{func_name}", names[{i}], "{ctype}", a[{i}]);'
            ).format(i=i, cvar=arg, func_name=pyname, ctype=astutils.Type(p))
      if i < minargs:
        # Non-default parameter.
        yield I+cvt
      else:
        yield I+'if (nargs > %d) {' % i
        # Check if we're passed kw args, skipping some default C++ args.
        # In this case we must substitute missed default args with default_value
        if (p.default_value == 'default'   # Matcher could not find the default.
            or 'inf' in p.default_value):  # W/A for b/29437257
          if n < nargs:
            yield I+I+('if (!a[{i}]) return DefaultArgMissedError('
                       '"{}", names[{i}]);'.format(pyname, i=i))
          yield I+I+cvt
        else:
          # C-cast takes care of the case where |arg| is an enum value, while
          # the matcher would return an integral literal. Using static_cast
          # would be ideal, but its argument should be an expression, which a
          # struct value like {1, 2, 3} is not.
          yield I+I+'if (!a[%d]) %s = (%s)%s;' % (i, arg, astutils.Type(p),
                                                  p.default_value)
          yield I+I+'else '+cvt
        yield I+'}'
  # Create input parameters for extra return values.
  return_type = astutils.FuncReturnType(func_ast)
  void_return_type = return_type == 'void'
  for n, p in enumerate(func_ast.returns):
    if n or void_return_type:
      yield I+'%s ret%d{};' % (astutils.Type(p), n)
      params.append('&ret%d' % n)
  yield I+'// Call actual C++ method.'
  if isinstance(call, list):
    for s in call[:-1]:
      yield I+s
    call = call[-1]
  if func_ast.async:
    if nargs:
      yield I+'Py_INCREF(args);'
      yield I+'Py_XINCREF(kw);'
    yield I+'PyThreadState* _save;'
    yield I+'Py_UNBLOCK_THREADS'
  optional_ret0 = False
  if (minargs < nargs or catch) and not void_return_type:
    if func_ast.returns[0].type.cpp_has_def_ctor:
      yield I+return_type+' ret0;'
    else:
      # Using optional<> requires T be have T(x) and T::op=(x) available.
      # While we need only t=x, implementing it will be a pain we skip for now.
      yield I+'::gtl::optional<%s> ret0;' % return_type
      optional_ret0 = True
  if catch:
    for s in _GenExceptionTry():
      yield s
  if minargs < nargs:
    if not void_return_type:
      call = 'ret0 = '+call
    yield I+'switch (nargs) {'
    for n in range(minargs, nargs+1):
      yield I+'case %d:' % n
      yield I+I+'%s; break;' % (call+astutils.TupleStr(params[:n]))
    yield I+'}'
  else:
    call += astutils.TupleStr(params)
    _I = I if catch else ''  # pylint: disable=invalid-name
    if void_return_type:
      yield _I+I+call+';'
    elif catch:
      yield _I+I+'ret0 = '+call+';'
    else:
      yield _I+I+return_type+' ret0 = '+call+';'
  if catch:
    for s in _GenExceptionCatch():
      yield s
  if postcall_init:
    if void_return_type:
      yield I+postcall_init
    else:
      yield I+'ret0'+postcall_init
  if func_ast.async:
    yield I+'Py_BLOCK_THREADS'
    if nargs:
      yield I+'Py_DECREF(args);'
      yield I+'Py_XDECREF(kw);'
  if catch:
    for s in _GenExceptionRaise():
      yield s
  # If ctxmgr, force return self on enter, None on exit.
  if nret > 1 or (func_ast.postproc or ctxmgr) and nret:
    yield I+'// Convert return values to Python.'
    yield I+'PyObject* p, * result_tuple = PyTuple_New(%d);' % nret
    yield I+'if (result_tuple == nullptr) return nullptr;'
    for i in range(nret):
      yield I+'if ((p=Clif_PyObjFrom(std::move(ret%d), %s)) == nullptr) {' % (
          i, postconv.Initializer(func_ast.returns[i].type, typepostconversion))
      yield I+I+'Py_DECREF(result_tuple);'
      yield I+I+'return nullptr;'
      yield I+'}'
      yield I+'PyTuple_SET_ITEM(result_tuple, %d, p);' % i
    if func_ast.postproc:
      yield I+'PyObject* pyproc = ImportFQName("%s");' % func_ast.postproc
      yield I+'if (pyproc == nullptr) {'
      yield I+I+'Py_DECREF(result_tuple);'
      yield I+I+'return nullptr;'
      yield I+'}'
      yield I+'p = PyObject_CallObject(pyproc, result_tuple);'
      yield I+'Py_DECREF(pyproc);'
      yield I+'Py_CLEAR(result_tuple);'
      if ctxmgr:
        yield I+'if (p == nullptr) return nullptr;'
        yield I+'Py_DECREF(p);  // Not needed by the context manager.'
      else:
        yield I+'result_tuple = p;'
    if ctxmgr == '__enter__@':
      yield I+'Py_XDECREF(result_tuple);'
      yield I+'Py_INCREF(self);'
      yield I+'return self;'
    elif ctxmgr == '__exit__@':
      yield I+'Py_XDECREF(result_tuple);'
      yield I+'Py_RETURN_NONE;'
    else:
      yield I+'return result_tuple;'
  elif nret:
    yield I+'return Clif_PyObjFrom(std::move(ret0%s), %s);' % (
        ('.value()' if optional_ret0 else ''),
        postconv.Initializer(func_ast.returns[0].type, typepostconversion))
  elif ctxmgr == '__enter__@':
    yield I+'Py_INCREF(self);'
    yield I+'return self;'
  else:
    yield I+'Py_RETURN_NONE;'
  yield '}'


def _GenExceptionTry():
  yield I+'PyObject* err_type = nullptr;'
  yield I+'string err_msg{"C++ exception"};'
  yield I+'try {'


def _GenExceptionCatch():
  yield I+'} catch(const std::exception& e) {'
  yield I+I+'err_type = PyExc_RuntimeError;'
  yield I+I+'err_msg += string(": ") + e.what();'
  yield I+'} catch (...) {'
  yield I+I+'err_type = PyExc_RuntimeError;'
  yield I+'}'


def _GenExceptionRaise():
  yield I+'if (err_type) {'
  yield I+I+'PyErr_SetString(err_type, err_msg.c_str());'
  yield I+I+'return nullptr;'
  yield I+'}'


def VirtualFunctionCall(fname, f, pyname, abstract, postconvinit):
  """Generate virtual redirector call wrapper from AST.FuncDecl f."""
  name = f.name.cpp_name
  ret = astutils.FuncReturnType(f, true_cpp_type=True)
  arg = astutils.FuncParamStr(f, 'a', true_cpp_type=True)
  mod = ['']
  if f.cpp_const_method: mod.append('const')
  if f.cpp_noexcept: mod.append('noexcept')
  yield ''
  yield I+'%s %s%s%s override {' % (ret, fname, arg, ' '.join(mod))
  params = astutils.TupleStr('std::move(a%i)' % i for i in range(
      len(f.params) + len(f.returns) - (ret != 'void')))
  yield I+I+('auto f = ::clif::SafeGetAttrString(pythis.get(), C("%s"));'
             % f.name.native)
  yield I+I+'if (f.get()) {'
  # TODO: Pass postconvinit(f.params...) to callback::Func.
  ret_st = 'return ' if ret != 'void' else ''
  yield I+I+I+'%s::clif::callback::Func<%s>(f.get())%s;' % (
      ret_st, ', '.join([ret] + list(astutils.Type(a) for a in f.params)
                        + list(astutils.FuncReturns(f))), params)
  yield I+I+'} else {'
  if abstract:
    # This is only called from C++. Since f has no info if it is pure virtual,
    # we can't always generate the call, so we always fail in an abstract class.
    yield I+I+I+('Py_FatalError("@virtual method %s.%s has no Python '
                 'implementation.");' % (pyname, f.name.native))
    # In Python 2 Py_FatalError is not marked __attribute__((__noreturn__)),
    # so to avoid -Wreturn-type warning add extra abort(). It does not hurt ;)
    yield I+I+I+'abort();'
  else:
    yield I+I+I+ret_st + name + params + ';'
  yield I+I+'}'
  yield I+'}'


def FromFunctionDef(ctype, wdef, wname, flags, doc):
  """PyCFunc definition."""
  assert ctype.startswith('std::function<'), repr(ctype)
  return 'static PyMethodDef %s = %s;' % (wdef, _DefLine('', wname, flags, doc))
