import os
import sys
import weakref
from datetime import timedelta
from itertools import chain
from threading import Lock

from werkzeug.datastructures import Headers
from werkzeug.datastructures import ImmutableDict
from werkzeug.exceptions import BadRequest
from werkzeug.exceptions import BadRequestKeyError
from werkzeug.exceptions import HTTPException
from werkzeug.exceptions import InternalServerError
from werkzeug.routing import BuildError
from werkzeug.routing import Map
from werkzeug.routing import RequestRedirect
from werkzeug.routing import RoutingException
from werkzeug.routing import Rule
from werkzeug.wrappers import BaseResponse

from . import cli
from . import json
from .config import Config
from .config import ConfigAttribute
from .ctx import _AppCtxGlobals
from .ctx import AppContext
from .ctx import RequestContext
from .globals import _request_ctx_stack
from .globals import g
from .globals import request
from .globals import session
from .helpers import find_package
from .helpers import get_debug_flag
from .helpers import get_env
from .helpers import get_flashed_messages
from .helpers import get_load_dotenv
from .helpers import locked_cached_property
from .helpers import url_for
from .json import jsonify
from .logging import create_logger
from .scaffold import _endpoint_from_view_func
from .scaffold import _sentinel
from .scaffold import Scaffold
from .scaffold import setupmethod
from .sessions import SecureCookieSessionInterface
from .signals import appcontext_tearing_down
from .signals import got_request_exception
from .signals import request_finished
from .signals import request_started
from .signals import request_tearing_down
from .templating import DispatchingJinjaLoader
from .templating import Environment
from .wrappers import Request
from .wrappers import Response


def _make_timedelta(value):
    if value is None or isinstance(value, timedelta):
        return value

    return timedelta(seconds=value)


class Flask(Scaffold):
    """flask类实现一个WSGI应用并充当其中心类。它传递给应用的module或者package。
    一旦创建成功，他将会充当中央注册表的视图功能、URL规则、模板配置等

    程序包的名称用于从内部或者模块所在的文件夹解析资源，这取决于包参数是否解析为
    一个实际的python包(一个带有:file: '摔打的文件夹)。或者是一个标准模块(只是一
    个' ')。py“文件)。
    
    
    关于更多资源加载信息，参考func:`opensource`
    
    通常您可以在main模块里创建一个flask实例，或者在软件包的文件夹下创建
    init.py文件用这种格式:
    

        from flask import Flask
        app = Flask(__name__)

    .. 警告:关于第一个参数

        第一个参数的意思是让flask了解什么属于你的应用。此名称用于寻找文件系统
        上的资源，也能用来通过拓展提高调试信息或者更多功能。
        
        因此，您在此处提供的内容非常重要。如果您使用单个模块，__name__总是正确的
        值。但是如你使用的是包，那么通常是推荐将包名硬解码到名称。
     
        举例来说如果您的应用是定义为文件：yourapplication/app.py,您应该以一下两
        种版本之一创建：  

            app = Flask('yourapplication')
            app = Flask(__name__.split('.')[0])
            
        为什么会是这样的?由于资源的查找方式，即便是__name__,应用也可以运行。然而
        这样会使调试更痛苦。某些拓展可以（假设）基于您的应用程序的导入名称。比如
        Flask-SQLAlchemy拓展将在您的应用程序中查找在调试模式下触发SQL查询的代码。
        如果导入名称设置的不正确，调试信息就会丢失。（举例来说它将只会挑选
        yourapplication.app中的sql查询而不会在yourapplication.views.frontend挑
        选)
       
       版本添加
    .. versionadded:: 0.7
       The `static_url_path`, `static_folder`, and `template_folder`
       parameters were added.

    .. versionadded:: 0.8
       The `instance_path` and `instance_relative_config` parameters were
       added.

    .. versionadded:: 0.11
       The `root_path` parameter was added.

    .. versionadded:: 1.0
       The ``host_matching`` and ``static_host`` parameters were added.

    .. versionadded:: 1.0
       The ``subdomain_matching`` parameter was added. Subdomain
       matching needs to be enabled manually now. Setting
       :data:`SERVER_NAME` does not implicitly enable it.

    :param import_name: 应用程序包的名称
    :param static_url_path: 可以用来指定网络上存放静态文件的不同路径。默认为名
                            称static_floder的文件夹
    :param static_folder: 包含静态文件的文件夹，位于static_url_path.相对与应用
                          程序的root_path,或绝对路径.默认为static
    :param static_host: 添加静态路由时要使用的主机.默认为None.在配置了
                        static_folder后，使用host_matching=True之后必需。
    :param host_matching: 设置url.map.host_matching参数，默认为False
    :param subdomain_matching: 当匹配routes时，考虑与SERVER_NAME的相关的子域，
                               默认为False   
    :param template_folder: 包含应用程序所使用的模板的文件夹.默认为应用程序根目
                            录下的templates文件夹                     
    :param instance_path: 应用程序的替代实例路径.默认情况下，包或模块旁边的文件
                          夹instance被假定为实例
    :param instance_relative_config: 如果设置为TRUE，则假设用于加载配置的相对文
                                     件名相对于实例路径，而不是应用程序根目录
    :param root_path: 应用程序文件的根目录路径The path to the root of the application files.
        只有不能被自动检测到的时候，才应该手动设置它，比如namespace包
    """

    #: 用于请求对象的类
    #: 有关更多信息，请参阅flask.Request
    request_class = Request

    #: 用于相应对象的类  
    #: 有关更多信息，请参阅flask.Response
    response_class = Response

    #: 用于jinjia环境设置的类
    #:
    #: .. 版本添加:: 0.11
    jinja_environment = Environment

    #: 用于flask.g实例的类.
    #:
    #: 自定义类的示例用例:
    #:
    #: 1. 将任意属性存储在flask.g.
    #: 2. 为每个请求的惰性数据库连接器添加一个属性.
    #: 3. 对意外的属性返回None而不是AttributeError
    #: 4. 如果设置了异常参数，则引发异常，一种可控的flask.g.
    #:
    #: 在Flask 0.9，这个属性叫做request_globals_class，但在1.0版本中被 
    #: 改成了app_ctx_globals_class。因为flask.g对象现在是应用于程序上下  
    #: 文域
    #:
    #: .. 版本添加:: 0.10
    app_ctx_globals_class = _AppCtxGlobals

    #: 用于应用程序config属性的类.
    #: 默认为flask.Config.
    #:
    #: 自定义类的示例用例:
    #:
    #: 1. 某些配置选项的默认值.
    #: 2. 通过键以外的属性访问配置值.
    #:
    #: .. 版本添加:: 0.11
    config_class = Config

    #: 测试标志位.将其设置为True以启用Flask拓展(将来有可能是Flask本身)的  
    #: 测试模式.
    #: 例如，这可能会激活具有额外运行时成本的测试帮助程序，而在默认情况下。
    #: 不应该启用这些程序
    #:
    #: 如果启用了该选项，并且没有从缺省值更改PROPAGATE_EXCEPTIONS，则隐式
    #: 启用该选项。
    #:
    #: 这个属性也可以在配置中以TESTING的选项关键字配置。默认为Flase。
    #: 
    testing = ConfigAttribute("TESTING")

    #: 如果设置了密钥，加密组件可以使用它来签名cookies和其内容。如果您想为
    #: 实例使用安全的cookies，请设置它为一个复杂的随机值.
    #:
    #: 也可以使用"SECRET_KEY"关键字从配置中配置此属性，默认为None。
    #: 
    secret_key = ConfigAttribute("SECRET_KEY")

    #: 安全cookies使用它作为会话cookies的名称
    #:
    #: 也可以使用"SESSION_COOKIE_NAME"关键字从配置中配置此属性，
    #: 默认为"session"
    session_cookie_name = ConfigAttribute("SESSION_COOKIE_NAME")

    #: 用于设置永久会话的过期日期的类`~datetime.timedelta`
    #: 默认为31天，永久会话大概可以生存31天 
    #: 
    #:
    #: 也可以使用"PERMANENT_SESSION_LIFETIME"关键字从配置中配置此属性，This attribute can also be configured from the config with the
    #: 默认为timedelta(days=31)。
    #: 
    permanent_session_lifetime = ConfigAttribute(
        "PERMANENT_SESSION_LIFETIME", get_converter=_make_timedelta
    )

    #: `~datetime.timedelta`类或者用于函数send_file的默认max_age秒数，
    #: 默认为None，告诉浏览器使用条件请求而不是定时缓存
    #: 
    #: 
    #:
    #: 使用"SEND_FILE_MAX_AGE_DEFAULT"关键字配置
    #: 
    #:
    #: .. 版本更新:: 2.0
    #:     默认为None而不是12小时
    send_file_max_age_default = ConfigAttribute(
        "SEND_FILE_MAX_AGE_DEFAULT", get_converter=_make_timedelta
    )

    #: 如果要使用X-Sendfile的功能，请启用这个.请注意服务器必须支持这一点。 
    #: 此功能仅影响通过send_file方法发送的文件
    #: 
    #:
    #: .. versionadded:: 0.2
    #:
    #: 也可以使用"USE_X_SENDFILE"关键字配置此属性。默认为False
    #: 
    use_x_sendfile = ConfigAttribute("USE_X_SENDFILE")

    #: 要使用的JSON编码器类.默认为类"~flask.json.JSONEncoder" 
    #:
    #: .. versionadded:: 0.10
    json_encoder = json.JSONEncoder

    #: 要使用的JSON解码器类.默认为类"~flask.json.JSONDecoder".
    #:
    #: .. versionadded:: 0.10
    json_decoder = json.JSONDecoder

    #: 通过`create_jinja_environment`方法传递给Jinja环境的选项.
    #: 在环境创建后（通过jiaja_env）修改这些选项不会生效. 
    #: 
    #: 
    #:
    #: .. versionchanged:: 1.1.0
    #:     从不可变字典变可变字典以允许更容易地配置.
    #:     
    #:
    jinja_options = {"extensions": ["jinja2.ext.autoescape", "jinja2.ext.with_"]}

    #: 默认配置参数.
    default_config = ImmutableDict(
        {
            "ENV": None,
            "DEBUG": None,
            "TESTING": False,
            "PROPAGATE_EXCEPTIONS": None,
            "PRESERVE_CONTEXT_ON_EXCEPTION": None,
            "SECRET_KEY": None,
            "PERMANENT_SESSION_LIFETIME": timedelta(days=31),
            "USE_X_SENDFILE": False,
            "SERVER_NAME": None,
            "APPLICATION_ROOT": "/",
            "SESSION_COOKIE_NAME": "session",
            "SESSION_COOKIE_DOMAIN": None,
            "SESSION_COOKIE_PATH": None,
            "SESSION_COOKIE_HTTPONLY": True,
            "SESSION_COOKIE_SECURE": False,
            "SESSION_COOKIE_SAMESITE": None,
            "SESSION_REFRESH_EACH_REQUEST": True,
            "MAX_CONTENT_LENGTH": None,
            "SEND_FILE_MAX_AGE_DEFAULT": None,
            "TRAP_BAD_REQUEST_ERRORS": None,
            "TRAP_HTTP_EXCEPTIONS": False,
            "EXPLAIN_TEMPLATE_LOADING": False,
            "PREFERRED_URL_SCHEME": "http",
            "JSON_AS_ASCII": True,
            "JSON_SORT_KEYS": True,
            "JSONIFY_PRETTYPRINT_REGULAR": False,
            "JSONIFY_MIMETYPE": "application/json",
            "TEMPLATES_AUTO_RELOAD": None,
            "MAX_COOKIE_SIZE": 4093,
        }
    )

    #: 用于创建URL规则的规则对象.通过`add_url_rule`方法使用.
    #: 默认为`werkzeug.routing.Rule`.
    #:
    #: .. versionadded:: 0.7
    url_rule_class = Rule

    #: 存储URL规则和路径设置参数的映射对象.
    #: 默认为`werkzeug.routing.Map`.
    #:
    #: .. versionadded:: 1.1.0
    url_map_class = Map

    #: 使用'test_client'时的测试客户端
    #:
    #: .. versionadded:: 0.7
    test_client_class = None

    #: `~click.testing.CliRunner`的子类,默认情况下由`test_cli_runner`方 
    #: 法使用的`~flask.testing.FlaskCliRunner`类.他的'__init__'方法应该将
    #: Flask程序对象作为第一个参数
    #:
    #:
    #: .. versionadded:: 1.0
    test_cli_runner_class = None

    #: 使用的会话接口.默认为此处使用的
    #: `~flask.sessions.SecureCookieSessionInterface`类的一个实例.
    #:
    #: .. versionadded:: 0.8
    session_interface = SecureCookieSessionInterface()

    # TODO 删除以下三个参数当Sphinx的继承成员运行时
    # https://github.com/sphinx-doc/sphinx/issues/741

    #: 此应用程序所属的程序包或模块的名称. 
    #: 一旦构造函数设置了它，请不要更改它.
    import_name = None

    #: 要添加到模板查找中的模板文件的位置.
    #: 如果不应该添加模板则为None.
    template_folder = None

    #: 文件系统上软件包的绝对路径. 
    #: 用于查找包中包含的资源.
    root_path = None

    def __init__(
        self,
        import_name,
        static_url_path=None,
        static_folder="static",
        static_host=None,
        host_matching=False,
        subdomain_matching=False,
        template_folder="templates",
        instance_path=None,
        instance_relative_config=False,
        root_path=None,
    ):
        super().__init__(
            import_name=import_name,
            static_folder=static_folder,
            static_url_path=static_url_path,
            template_folder=template_folder,
            root_path=root_path,
        )

        if instance_path is None:
            instance_path = self.auto_find_instance_path()
        elif not os.path.isabs(instance_path):
            raise ValueError(
                "If an instance path is provided it must be absolute."
                " A relative path was given instead."
            )

        #: 保存实例文件的文件夹路径.
        #:
        #: .. versionadded:: 0.8
        self.instance_path = instance_path

        #: 配置字典为`Config`类.它的行为完全像是一个普通字典，却支持额外的从
        #: 文件载入配置的方法.
        #: 
        self.config = self.make_config(instance_relative_config)

        #: 当`url_for`引发`~werkzeug.routing.BuildError`错误时所调用的函数列表.
        #: 在这里注册的每个函数都以`error`, `endpoint` 和`values`调用. 
        #: 如果一个函数返回'None'或者引起一个`BuildError`，下一个函数将会尝试.
        #: 
        #: 
        #:
        #: .. versionadded:: 0.9
        self.url_build_error_handlers = []

        #: 此实例第一个请求开始时所调用的函数列表.
        #: 要注册函数，请使用`before_first_request`装饰符.
        #: 
        #:
        #: .. versionadded:: 0.8
        self.before_first_request_funcs = []

        #: 应用程序上下文销毁时所调用的函数列表.
        #: 由于在请求结束时，应用程序上下文也会被销毁，所以这里也用来存放断开
        #: 数据库连接的代码.
        #: 
        #:
        #: .. versionadded:: 0.9
        self.teardown_appcontext_funcs = []

        #: 当一个shell上下文创建时应该运行的shell上下文处理程序函数列表. 
        #: 
        #:
        #: .. versionadded:: 0.11
        self.shell_context_processors = []

        #: 字典中按名称列出的所有附属蓝图. 蓝图可以被多次附加所以这个字典不能
        #: 告诉您多久他们被附加一次（频率）.
        #: 
        #:
        #: .. versionadded:: 0.7
        self.blueprints = {}
        self._blueprint_order = []

        #: 扩展可以存储应用程序特定状态的地方. 
        #: 例如,拓展可以在这里存放数据库引擎和类似的东西.
        #: 
        #:
        #: 关键字必须和拓展模块的名称匹配. 
        #: 例如,对于`flask_foo`拓展中的"Flask-Foo" 关键字应该为'foo'.
        #: 
        #:
        #: .. versionadded:: 0.7
        self.extensions = {}

        #: `~werkzeug.routing.Map`类的实例.您可以在创建类之后，在任何路由连接
        #: 之前，使用它来更改路由转换器。
        #: Example::
        #:
        #:    from werkzeug.routing import BaseConverter
        #:
        #:    class ListConverter(BaseConverter):
        #:        def to_python(self, value):
        #:            return value.split(',')
        #:        def to_url(self, values):
        #:            return ','.join(super(ListConverter, self).to_url(value)
        #:                            for value in values)
        #:
        #:    app = Flask(__name__)
        #:    app.url_map.converters['list'] = ListConverter
        self.url_map = self.url_map_class()

        self.url_map.host_matching = host_matching
        self.subdomain_matching = subdomain_matching

        # 在内部跟着应用程序是否已经处理了至少一个请求.
        # 
        self._got_first_request = False
        self._before_request_lock = Lock()

        # 如果由配置static_folder,则使用提供的static_url_path,static_host,
        # static_folder配置静态路由   
        # 请注意，我们这样做是在不检查static_folder是否存在的情况下进行的.
        # 例如，它可能是在服务器运行的时候创建的（例如开发）。
        # 另外，谷歌应用引擎将静态文件存储在某处.
        if self.has_static_folder:
            assert (
                bool(static_host) == host_matching
            ), "Invalid static_host/host_matching combination"
            # Use a weakref to avoid creating a reference cycle between the app
            # and the view function (see #3761).
            self_ref = weakref.ref(self)
            self.add_url_rule(
                f"{self.static_url_path}/<path:filename>",
                endpoint="static",
                host=static_host,
                view_func=lambda **kw: self_ref().send_static_file(**kw),
            )

        # 设置Clkck组的名称，以防有人想要将应用程序的命令添加到另一个CLI工具。
        #
        self.cli.name = self.name

    def _is_setup_finished(self):
        return self.debug and self._got_first_request

    @locked_cached_property
    def name(self):
        """应用程序名称.这通常是导入名称，不同之处在于如果导入名称为main，
        则从运行文件中猜测它。这个名称被用作显示名称当Flask需要应用程序的名称时.
        可以被设置和覆盖来改变它的值.
        

        .. versionadded:: 0.8
        """
        if self.import_name == "__main__":
            fn = getattr(sys.modules["__main__"], "__file__", None)
            if fn is None:
                return "__main__"
            return os.path.splitext(os.path.basename(fn))[0]
        return self.import_name

    @property
    def propagate_exceptions(self):
        """如果配置过，返回``PROPAGATE_EXCEPTIONS``配置的值，否则返回合理的默认值. 

        .. versionadded:: 0.7
        """
        rv = self.config["PROPAGATE_EXCEPTIONS"]
        if rv is not None:
            return rv
        return self.testing or self.debug

    @property
    def preserve_context_on_exception(self):
        """如果设置过，返回``PRESERVE_CONTEXT_ON_EXCEPTION``配置值，否则返回合
        理的默认值. 
        

        .. versionadded:: 0.7
        """
        rv = self.config["PRESERVE_CONTEXT_ON_EXCEPTION"]
        if rv is not None:
            return rv
        return self.debug

    @locked_cached_property
    def logger(self):
        """一个标准python类`~logging.Logger`对于应用程序,与参数`name`有相同名称.
        

        在debug模式下, 日志的参数`~logging.Logger.level` 将会设置为`~logging.DEBUG`.

        如果未配置任何处理程序，则添加默认处理程序为.有关更多信息，请参见`/logging`.

        .. versionchanged:: 1.1.0
            The logger takes the same name as :attr:`name` rather than
            hard-coding ``"flask.app"``.

        .. versionchanged:: 1.0.0
            行为是简化过的. 记录器总是命名为"flask.app".仅在配置期间设置级别，而不会
            每次都检查`app.debug`.只使用一种格式，而不是根据`app.debug`设置不同格式.
            不删除任何处理程序，并且只在没有配置处理程序时添加处理程序
            

        .. versionadded:: 0.3
        """
        return create_logger(self)

    @locked_cached_property
    def jinja_env(self):
        """用于加载模板的Jinja环境.

        环境在第一次访问此属性时创建.改变属性`jinja_options`后不再生效.
        """
        return self.create_jinja_environment()

    @property
    def got_first_request(self):
        """本属性设置为 `True`如果应用开始接收第一个请求
        .. versionadded:: 0.8
        """
        return self._got_first_request

    def make_config(self, instance_relative=False):
        """用于由Flask构造函数创建config属性.
        `instance_relative` 参数是从Flask构造函数传入的这里叫 `instance_relative_config`) 
        并指出是否配置应该相对于应用程序的实例路径或根路径



        .. versionadded:: 0.8
        """
        root_path = self.root_path
        if instance_relative:
            root_path = self.instance_path
        defaults = dict(self.default_config)
        defaults["ENV"] = get_env()
        defaults["DEBUG"] = get_debug_flag()
        return self.config_class(root_path, defaults)

    def auto_find_instance_path(self):
        """尝试查找实例路径如果它未提供给实例路径应用程序类的构造函数.  
        它基本上会计算主文件旁边的名为``instance''的文件夹的路径或包
        

        .. versionadded:: 0.8
        """
        prefix, package_path = find_package(self.import_name)
        if prefix is None:
            return os.path.join(package_path, "instance")
        return os.path.join(prefix, "var", f"{self.name}-instance")

    def open_instance_resource(self, resource, mode="rb"):
        """从应用程序的实例文件夹中打开资源
        (:attr:`instance_path`).  否则像
        :meth:`open_resource`. 实例资源也可以为了写入打开.

        :param resource: 资源名.  访问其中的资源子文件夹，使用正斜杠作为分隔符.
        :param mode: 资源文件打开模式，默认为 'rb'.
        """
        return open(os.path.join(self.instance_path, resource), mode)

    @property
    def templates_auto_reload(self):
        """更改模板时重新加载模板. 用于
        :meth:`create_jinja_environment`.

        可以使用以下方式配置此属性:data:`TEMPLATES_AUTO_RELOAD`.
        如果未设置，它将在调试模式下启用.

        .. versionadded:: 1.0
            添加了此属性，但基础配置和行为已经存在.
        """
        rv = self.config["TEMPLATES_AUTO_RELOAD"]
        return rv if rv is not None else self.debug

    @templates_auto_reload.setter
    def templates_auto_reload(self, value):
        self.config["TEMPLATES_AUTO_RELOAD"] = value

    def create_jinja_environment(self):
        """创建基于`jinja_options`的Jinja环境以及该应用程序
        的各种与Jinja相关的方法. 在创建之后改变参数`jinja_options`不会生效. 
        还向环境添加Flask相关的全局变量和过滤器.

        .. versionchanged:: 0.11
           ``Environment.auto_reload`` 可依据
           ``TEMPLATES_AUTO_RELOAD`` 配置选项来进行设置.

        .. versionadded:: 0.5
        """
        options = dict(self.jinja_options)

        if "autoescape" not in options:
            options["autoescape"] = self.select_jinja_autoescape

        if "auto_reload" not in options:
            options["auto_reload"] = self.templates_auto_reload

        rv = self.jinja_environment(self, **options)
        rv.globals.update(
            url_for=url_for,
            get_flashed_messages=get_flashed_messages,
            config=self.config,
            # 出于效率考虑，request、session和g通常是与上下文处理
            # 器一起添加的，但对于导入的模板，我们也希望在其中添
            # 加代理
            request=request,
            session=session,
            g=g,
        )
        rv.filters["tojson"] = json.tojson_filter
        return rv

    def create_global_jinja_loader(self):
        """为Jinja2环境创建加载程序. 
        可以用来仅覆盖加载程序其余的保持不变.  
        不鼓励重写此功能.  
        相反，应该覆盖`jinja_loader` 方法函数.

        全局加载器在应用程序的加载器和各个蓝图之间进行分派.

        .. versionadded:: 0.7
        """
        return DispatchingJinjaLoader(self)

    def select_jinja_autoescape(self, filename):
        """返回 ``True``如果自动转义应该在给定模板名称的情况下处于活动状态
        . 如果未提供模板名称，则返回`True`.

        .. versionadded:: 0.5
        """
        if filename is None:
            return True
        return filename.endswith((".html", ".htm", ".xml", ".xhtml"))

    def update_template_context(self, context):
        """使用一些常用变量更新模板上下文.
        它会将请求、会话、配置和g注入到模板上下文中，
        以及所有模板上下文处理器想要注入的内容. 
        请注意，Flask 0.6版本开始，原始值在上下文中不会被
        覆盖如果上下文处理器决定返回具有相同键的值.

        :param context: 将上下文作为字典，在适当的地方进行更新，以添加额外的变量.
        """
        funcs = self.template_context_processors[None]
        reqctx = _request_ctx_stack.top
        if reqctx is not None:
            bp = reqctx.request.blueprint
            if bp is not None and bp in self.template_context_processors:
                funcs = chain(funcs, self.template_context_processors[bp])
        orig_ctx = context.copy()
        for func in funcs:
            context.update(func())
        # make sure the original values win.  This makes it possible to
        # easier add new variables in context processors without breaking
        # existing views.
        context.update(orig_ctx)

    def make_shell_context(self):
        """返回此应用程序的交互式shell的shell上下文.
        这将运行所有注册的shell上下文处理器.  

        .. versionadded:: 0.11
        """
        rv = {"app": self, "g": g}
        for processor in self.shell_context_processors:
            rv.update(processor())
        return rv

    #: 不论应用程序在什么环境中运行. Flask和拓展们可以启用
    #: 基于环境的行为, 例如启用调试模式
    #: 这映射到ENV配置键. This is set by the
    #: 这是由`FLASK_ENV`环境变量设置
    #: 如果在代码中设置，可能不会像预期的那样行为.
    #:
    #: **在生产环境中部署时请勿启用开发.**
    #:
    #: Default: ``'production'``
    env = ConfigAttribute("ENV")

    @property
    def debug(self):
        """不论调试模式是否启动.当使用 ``flask run`` 来开启
        开发服务器,将显示一个交互式调试器，用于未处理的异常, 
        并在代码加载时重新加载服务器变化. 
        这映射到：DEBUG`DEBUG`配置键. 
        当环境变量为``'development'``时启用
        通过``FLASK_DEBUG``环境变量进行覆盖. 
        如果在代码中设置，可能不会像预期的那样行为.

        **在生产环境中部署时不要启用调试模式.**

        默认值：True，如果：attr：`env`是'development'，否则为``False''
        
        """
        return self.config["DEBUG"]

    @debug.setter
    def debug(self, value):
        self.config["DEBUG"] = value
        self.jinja_env.auto_reload = self.templates_auto_reload

    def run(self, host=None, port=None, debug=None, load_dotenv=True, **options):
        """在本地开发服务器上运行应用程序.

        不要在生产设置中使用``run（）``. 
        不旨在满足生产服务器的安全性和性能要求.
        相反，请参阅doc：`/ deploying / index`以获取WSGI服务器建议.

        如果参数`debug` 标志已设置，服务器将会自动重载当
        进行代码更改时并在发生异常的情况下显示调试器.

        如果要在调试模式下运行应用程序, 但禁用在交互式调试器上执行代码, 
        你可以传递
        ``use_evalex=False`` 作为参数.  
        这将保留调试器的追溯屏幕处于活动状态,但是禁用代码执行.

        不建议使用此功能进行开发自动重新加载，因为此功能支持很差.  
        相反你应该使用Flask命令行脚本的run支持。
        

        .. 警告:: 记住

           Flask将通过通用错误页面抑制任何服务器错误除非它处于调试模式.  
           因此，仅启用无需代码重新加载的交互式调试器, 
           你必须调用带``debug=True``和 ``use_reloader=False``的run方法.
           不在调试模式设置 ``use_debugger`` 为 ``True`` 
           不会捕获任何异常，因为不会有任何捕获.

        :param host: 要监听的主机名. 设置为 ``'0.0.0.0'`` 来
            使服务器在外部可用. 
            默认为``'127.0.0.1'`` 或者 ``SERVER_NAME`` 配置变量
            如果有的话.
        :param port: Web服务器的端口. 默认为 ``5000`` 或者
            端口定义在 ``SERVER_NAME`` 配置变量中如果有.
        :param debug: 如果给定，则启用或禁用调试模式. 参考
            :attr:`debug`.
        :param load_dotenv: 加载最近的.env和.flaskenv设置环境变量的文件. 
            也会改变工作目录为包含找到的第一个文件的目录.
        :param options: 将要转发给基础Werkzeug服务器的选项. 
            参考函数`werkzeug.serving.run_simple` 获取更多信息.

        .. versionchanged:: 1.0
            如果已安装, python-dotenv将用于从文件.env和.flaskenv加载环境变量.

            如果已设置,环境变量`FLASK_ENV`和`FLASK_DEBUG`
            将会覆盖`env`和`debug`.

            Threaded mode is enabled by default.

        .. versionchanged:: 0.10
            The default port is now picked from the ``SERVER_NAME``
            variable.
        """
        # 请将其更改为无操作如果服务器从命令行调用.
        # 请查看cli.py了解更多信息.
        if os.environ.get("FLASK_RUN_FROM_CLI") == "true":
            from .debughelpers import explain_ignored_app_run

            explain_ignored_app_run()
            return

        if get_load_dotenv(load_dotenv):
            cli.load_dotenv()

            # 一旦设置, 允许环境变量覆盖之前的值
            if "FLASK_ENV" in os.environ:
                self.env = get_env()
                self.debug = get_debug_flag()
            elif "FLASK_DEBUG" in os.environ:
                self.debug = get_debug_flag()

        # 传递给方法的调试覆盖所有其他来源
        if debug is not None:
            self.debug = bool(debug)

        server_name = self.config.get("SERVER_NAME")
        sn_host = sn_port = None

        if server_name:
            sn_host, _, sn_port = server_name.partition(":")

        if not host:
            if sn_host:
                host = sn_host
            else:
                host = "127.0.0.1"

        if port or port == 0:
            port = int(port)
        elif sn_port:
            port = int(sn_port)
        else:
            port = 5000

        options.setdefault("use_reloader", self.debug)
        options.setdefault("use_debugger", self.debug)
        options.setdefault("threaded", True)

        cli.show_server_banner(self.env, self.debug, self.name, False)

        from werkzeug.serving import run_simple

        try:
            run_simple(host, port, self, **options)
        finally:
            # 如果开发服务器正常重置，重置第一个请求信息
            # 这使得没有重载器和shell交互的服务器的重启成为可能
           
            self._got_first_request = False

    def test_client(self, use_cookies=True, **kwargs):
        """为应用创建测试客户端.  关于单元测试的信息请查看doc:`/testing`.

        请注意如果您在测试声明或异常在您的应用代码里，您必须设置``app.testing = True``
        来传播异常到测试的客户端。不然异常会由应用（测试客户端看不到）处理，而且
        声明异常或者其他异常的唯一指示将会变成500状态码响应给测试客户端。更多请查看
        `testing`属性。举例来说：
       

            app.testing = True
            client = app.test_client()

        测试客户端能用在``with``块中推迟关闭上下文直到``with``块结束. 
        这很有用如果你想传递本地上下文来测试:

            with app.test_client() as c:
                rv = c.get('/?vodka=42')
                assert request.args['vodka'] == '42'

        此外，您可以传递可选的关键字参数，然后传递给应用程序的' test_client_class '构造函数.
        举例:

            from flask.testing import FlaskClient

            class CustomClient(FlaskClient):
                def __init__(self, *args, **kwargs):
                    self._authentication = kwargs.pop("authentication")
                    super(CustomClient,self).__init__( *args, **kwargs)

            app.test_client_class = CustomClient
            client = app.test_client(authentication='Basic ....')

        See :class:`~flask.testing.FlaskClient` for more information.

        .. versionchanged:: 0.4
           added support for ``with`` block usage for the client.

        .. versionadded:: 0.7
           The `use_cookies` parameter was added as well as the ability
           to override the client to be used by setting the
           :attr:`test_client_class` attribute.

        .. versionchanged:: 0.11
           Added `**kwargs` to support passing additional keyword arguments to
           the constructor of :attr:`test_client_class`.
        """
        cls = self.test_client_class
        if cls is None:
            from .testing import FlaskClient as cls
        return cls(self, self.response_class, use_cookies=use_cookies, **kwargs)

    def test_cli_runner(self, **kwargs):
        """Create a CLI runner for testing CLI commands.
        See :ref:`testing-cli`.

        Returns an instance of :attr:`test_cli_runner_class`, by default
        :class:`~flask.testing.FlaskCliRunner`. The Flask app object is
        passed as the first argument.

        .. versionadded:: 1.0
        """
        cls = self.test_cli_runner_class

        if cls is None:
            from .testing import FlaskCliRunner as cls

        return cls(self, **kwargs)

    @setupmethod
    def register_blueprint(self, blueprint, **options):
        """Register a :class:`~flask.Blueprint` on the application. Keyword
        arguments passed to this method will override the defaults set on the
        blueprint.

        Calls the blueprint's :meth:`~flask.Blueprint.register` method after
        recording the blueprint in the application's :attr:`blueprints`.

        :param blueprint: The blueprint to register.
        :param url_prefix: Blueprint routes will be prefixed with this.
        :param subdomain: Blueprint routes will match on this subdomain.
        :param url_defaults: Blueprint routes will use these default values for
            view arguments.
        :param options: Additional keyword arguments are passed to
            :class:`~flask.blueprints.BlueprintSetupState`. They can be
            accessed in :meth:`~flask.Blueprint.record` callbacks.

        .. versionadded:: 0.7
        """
        first_registration = False

        if blueprint.name in self.blueprints:
            assert self.blueprints[blueprint.name] is blueprint, (
                "A name collision occurred between blueprints"
                f" {blueprint!r} and {self.blueprints[blueprint.name]!r}."
                f" Both share the same name {blueprint.name!r}."
                f" Blueprints that are created on the fly need unique"
                f" names."
            )
        else:
            self.blueprints[blueprint.name] = blueprint
            self._blueprint_order.append(blueprint)
            first_registration = True

        blueprint.register(self, options, first_registration)

    def iter_blueprints(self):
        """Iterates over all blueprints by the order they were registered.

        .. versionadded:: 0.11
        """
        return iter(self._blueprint_order)

    @setupmethod
    def add_url_rule(
        self,
        rule,
        endpoint=None,
        view_func=None,
        provide_automatic_options=None,
        **options,
    ):
        """Connects a URL rule.  Works exactly like the :meth:`route`
        decorator.  If a view_func is provided it will be registered with the
        endpoint.

        Basically this example::

            @app.route('/')
            def index():
                pass

        Is equivalent to the following::

            def index():
                pass
            app.add_url_rule('/', 'index', index)

        If the view_func is not provided you will need to connect the endpoint
        to a view function like so::

            app.view_functions['index'] = index

        Internally :meth:`route` invokes :meth:`add_url_rule` so if you want
        to customize the behavior via subclassing you only need to change
        this method.

        For more information refer to :ref:`url-route-registrations`.

        .. versionchanged:: 0.2
           `view_func` parameter added.

        .. versionchanged:: 0.6
           ``OPTIONS`` is added automatically as method.

        :param rule: the URL rule as string
        :param endpoint: the endpoint for the registered URL rule.  Flask
                         itself assumes the name of the view function as
                         endpoint
        :param view_func: the function to call when serving a request to the
                          provided endpoint
        :param provide_automatic_options: controls whether the ``OPTIONS``
            method should be added automatically. This can also be controlled
            by setting the ``view_func.provide_automatic_options = False``
            before adding the rule.
        :param options: the options to be forwarded to the underlying
                        :class:`~werkzeug.routing.Rule` object.  A change
                        to Werkzeug is handling of method options.  methods
                        is a list of methods this rule should be limited
                        to (``GET``, ``POST`` etc.).  By default a rule
                        just listens for ``GET`` (and implicitly ``HEAD``).
                        Starting with Flask 0.6, ``OPTIONS`` is implicitly
                        added and handled by the standard request handling.
        """
        if endpoint is None:
            endpoint = _endpoint_from_view_func(view_func)
        options["endpoint"] = endpoint
        methods = options.pop("methods", None)

        # if the methods are not given and the view_func object knows its
        # methods we can use that instead.  If neither exists, we go with
        # a tuple of only ``GET`` as default.
        if methods is None:
            methods = getattr(view_func, "methods", None) or ("GET",)
        if isinstance(methods, str):
            raise TypeError(
                "Allowed methods must be a list of strings, for"
                ' example: @app.route(..., methods=["POST"])'
            )
        methods = {item.upper() for item in methods}

        # Methods that should always be added
        required_methods = set(getattr(view_func, "required_methods", ()))

        # starting with Flask 0.8 the view_func object can disable and
        # force-enable the automatic options handling.
        if provide_automatic_options is None:
            provide_automatic_options = getattr(
                view_func, "provide_automatic_options", None
            )

        if provide_automatic_options is None:
            if "OPTIONS" not in methods:
                provide_automatic_options = True
                required_methods.add("OPTIONS")
            else:
                provide_automatic_options = False

        # Add the required methods now.
        methods |= required_methods

        rule = self.url_rule_class(rule, methods=methods, **options)
        rule.provide_automatic_options = provide_automatic_options

        self.url_map.add(rule)
        if view_func is not None:
            old_func = self.view_functions.get(endpoint)
            if old_func is not None and old_func != view_func:
                raise AssertionError(
                    "View function mapping is overwriting an existing"
                    f" endpoint function: {endpoint}"
                )
            self.view_functions[endpoint] = view_func

    @setupmethod
    def template_filter(self, name=None):
        """A decorator that is used to register custom template filter.
        You can specify a name for the filter, otherwise the function
        name will be used. Example::

          @app.template_filter()
          def reverse(s):
              return s[::-1]

        :param name: the optional name of the filter, otherwise the
                     function name will be used.
        """

        def decorator(f):
            self.add_template_filter(f, name=name)
            return f

        return decorator

    @setupmethod
    def add_template_filter(self, f, name=None):
        """Register a custom template filter.  Works exactly like the
        :meth:`template_filter` decorator.

        :param name: the optional name of the filter, otherwise the
                     function name will be used.
        """
        self.jinja_env.filters[name or f.__name__] = f

    @setupmethod
    def template_test(self, name=None):
        """A decorator that is used to register custom template test.
        You can specify a name for the test, otherwise the function
        name will be used. Example::

          @app.template_test()
          def is_prime(n):
              if n == 2:
                  return True
              for i in range(2, int(math.ceil(math.sqrt(n))) + 1):
                  if n % i == 0:
                      return False
              return True

        .. versionadded:: 0.10

        :param name: the optional name of the test, otherwise the
                     function name will be used.
        """

        def decorator(f):
            self.add_template_test(f, name=name)
            return f

        return decorator

    @setupmethod
    def add_template_test(self, f, name=None):
        """Register a custom template test.  Works exactly like the
        :meth:`template_test` decorator.

        .. versionadded:: 0.10

        :param name: the optional name of the test, otherwise the
                     function name will be used.
        """
        self.jinja_env.tests[name or f.__name__] = f

    @setupmethod
    def template_global(self, name=None):
        """A decorator that is used to register a custom template global function.
        You can specify a name for the global function, otherwise the function
        name will be used. Example::

            @app.template_global()
            def double(n):
                return 2 * n

        .. versionadded:: 0.10

        :param name: the optional name of the global function, otherwise the
                     function name will be used.
        """

        def decorator(f):
            self.add_template_global(f, name=name)
            return f

        return decorator

    @setupmethod
    def add_template_global(self, f, name=None):
        """Register a custom template global function. Works exactly like the
        :meth:`template_global` decorator.

        .. versionadded:: 0.10

        :param name: the optional name of the global function, otherwise the
                     function name will be used.
        """
        self.jinja_env.globals[name or f.__name__] = f

    @setupmethod
    def before_first_request(self, f):
        """Registers a function to be run before the first request to this
        instance of the application.

        The function will be called without any arguments and its return
        value is ignored.

        .. versionadded:: 0.8
        """
        self.before_first_request_funcs.append(f)
        return f

    @setupmethod
    def teardown_appcontext(self, f):
        """Registers a function to be called when the application context
        ends.  These functions are typically also called when the request
        context is popped.

        Example::

            ctx = app.app_context()
            ctx.push()
            ...
            ctx.pop()

        When ``ctx.pop()`` is executed in the above example, the teardown
        functions are called just before the app context moves from the
        stack of active contexts.  This becomes relevant if you are using
        such constructs in tests.

        Since a request context typically also manages an application
        context it would also be called when you pop a request context.

        When a teardown function was called because of an unhandled exception
        it will be passed an error object. If an :meth:`errorhandler` is
        registered, it will handle the exception and the teardown will not
        receive it.

        The return values of teardown functions are ignored.

        .. versionadded:: 0.9
        """
        self.teardown_appcontext_funcs.append(f)
        return f

    @setupmethod
    def shell_context_processor(self, f):
        """Registers a shell context processor function.

        .. versionadded:: 0.11
        """
        self.shell_context_processors.append(f)
        return f

    def _find_error_handler(self, e):
        """Return a registered error handler for an exception in this order:
        blueprint handler for a specific code, app handler for a specific code,
        blueprint handler for an exception class, app handler for an exception
        class, or ``None`` if a suitable handler is not found.
        """
        exc_class, code = self._get_exc_class_and_code(type(e))

        for name, c in (
            (request.blueprint, code),
            (None, code),
            (request.blueprint, None),
            (None, None),
        ):
            handler_map = self.error_handler_spec.setdefault(name, {}).get(c)

            if not handler_map:
                continue

            for cls in exc_class.__mro__:
                handler = handler_map.get(cls)

                if handler is not None:
                    return handler

    def handle_http_exception(self, e):
        """Handles an HTTP exception.  By default this will invoke the
        registered error handlers and fall back to returning the
        exception as response.

        .. versionchanged:: 1.0.3
            ``RoutingException``, used internally for actions such as
             slash redirects during routing, is not passed to error
             handlers.

        .. versionchanged:: 1.0
            Exceptions are looked up by code *and* by MRO, so
            ``HTTPExcpetion`` subclasses can be handled with a catch-all
            handler for the base ``HTTPException``.

        .. versionadded:: 0.3
        """
        # Proxy exceptions don't have error codes.  We want to always return
        # those unchanged as errors
        if e.code is None:
            return e

        # RoutingExceptions are used internally to trigger routing
        # actions, such as slash redirects raising RequestRedirect. They
        # are not raised or handled in user code.
        if isinstance(e, RoutingException):
            return e

        handler = self._find_error_handler(e)
        if handler is None:
            return e
        return handler(e)

    def trap_http_exception(self, e):
        """Checks if an HTTP exception should be trapped or not.  By default
        this will return ``False`` for all exceptions except for a bad request
        key error if ``TRAP_BAD_REQUEST_ERRORS`` is set to ``True``.  It
        also returns ``True`` if ``TRAP_HTTP_EXCEPTIONS`` is set to ``True``.

        This is called for all HTTP exceptions raised by a view function.
        If it returns ``True`` for any exception the error handler for this
        exception is not called and it shows up as regular exception in the
        traceback.  This is helpful for debugging implicitly raised HTTP
        exceptions.

        .. versionchanged:: 1.0
            Bad request errors are not trapped by default in debug mode.

        .. versionadded:: 0.8
        """
        if self.config["TRAP_HTTP_EXCEPTIONS"]:
            return True

        trap_bad_request = self.config["TRAP_BAD_REQUEST_ERRORS"]

        # if unset, trap key errors in debug mode
        if (
            trap_bad_request is None
            and self.debug
            and isinstance(e, BadRequestKeyError)
        ):
            return True

        if trap_bad_request:
            return isinstance(e, BadRequest)

        return False

    def handle_user_exception(self, e):
        """This method is called whenever an exception occurs that
        should be handled. A special case is :class:`~werkzeug
        .exceptions.HTTPException` which is forwarded to the
        :meth:`handle_http_exception` method. This function will either
        return a response value or reraise the exception with the same
        traceback.

        .. versionchanged:: 1.0
            Key errors raised from request data like ``form`` show the
            bad key in debug mode rather than a generic bad request
            message.

        .. versionadded:: 0.7
        """
        if isinstance(e, BadRequestKeyError):
            if self.debug or self.config["TRAP_BAD_REQUEST_ERRORS"]:
                e.show_exception = True

                # Werkzeug < 0.15 doesn't add the KeyError to the 400
                # message, add it in manually.
                # TODO: clean up once Werkzeug >= 0.15.5 is required
                if e.args[0] not in e.get_description():
                    e.description = f"KeyError: {e.args[0]!r}"
            elif not hasattr(BadRequestKeyError, "show_exception"):
                e.args = ()

        if isinstance(e, HTTPException) and not self.trap_http_exception(e):
            return self.handle_http_exception(e)

        handler = self._find_error_handler(e)

        if handler is None:
            raise

        return handler(e)

    def handle_exception(self, e):
        """Handle an exception that did not have an error handler
        associated with it, or that was raised from an error handler.
        This always causes a 500 ``InternalServerError``.

        Always sends the :data:`got_request_exception` signal.

        If :attr:`propagate_exceptions` is ``True``, such as in debug
        mode, the error will be re-raised so that the debugger can
        display it. Otherwise, the original exception is logged, and
        an :exc:`~werkzeug.exceptions.InternalServerError` is returned.

        If an error handler is registered for ``InternalServerError`` or
        ``500``, it will be used. For consistency, the handler will
        always receive the ``InternalServerError``. The original
        unhandled exception is available as ``e.original_exception``.

        .. note::
            Prior to Werkzeug 1.0.0, ``InternalServerError`` will not
            always have an ``original_exception`` attribute. Use
            ``getattr(e, "original_exception", None)`` to simulate the
            behavior for compatibility.

        .. versionchanged:: 1.1.0
            Always passes the ``InternalServerError`` instance to the
            handler, setting ``original_exception`` to the unhandled
            error.

        .. versionchanged:: 1.1.0
            ``after_request`` functions and other finalization is done
            even for the default 500 response when there is no handler.

        .. versionadded:: 0.3
        """
        exc_info = sys.exc_info()
        got_request_exception.send(self, exception=e)

        if self.propagate_exceptions:
            # Re-raise if called with an active exception, otherwise
            # raise the passed in exception.
            if exc_info[1] is e:
                raise

            raise e

        self.log_exception(exc_info)
        server_error = InternalServerError()
        # TODO: pass as param when Werkzeug>=1.0.0 is required
        # TODO: also remove note about this from docstring and docs
        server_error.original_exception = e
        handler = self._find_error_handler(server_error)

        if handler is not None:
            server_error = handler(server_error)

        return self.finalize_request(server_error, from_error_handler=True)

    def log_exception(self, exc_info):
        """Logs an exception.  This is called by :meth:`handle_exception`
        if debugging is disabled and right before the handler is called.
        The default implementation logs the exception as error on the
        :attr:`logger`.

        .. versionadded:: 0.8
        """
        self.logger.error(
            f"Exception on {request.path} [{request.method}]", exc_info=exc_info
        )

    def raise_routing_exception(self, request):
        """Exceptions that are recording during routing are reraised with
        this method.  During debug we are not reraising redirect requests
        for non ``GET``, ``HEAD``, or ``OPTIONS`` requests and we're raising
        a different error instead to help debug situations.

        :internal:
        """
        if (
            not self.debug
            or not isinstance(request.routing_exception, RequestRedirect)
            or request.method in ("GET", "HEAD", "OPTIONS")
        ):
            raise request.routing_exception

        from .debughelpers import FormDataRoutingRedirect

        raise FormDataRoutingRedirect(request)

    def dispatch_request(self):
        """Does the request dispatching.  Matches the URL and returns the
        return value of the view or error handler.  This does not have to
        be a response object.  In order to convert the return value to a
        proper response object, call :func:`make_response`.

        .. versionchanged:: 0.7
           This no longer does the exception handling, this code was
           moved to the new :meth:`full_dispatch_request`.
        """
        req = _request_ctx_stack.top.request
        if req.routing_exception is not None:
            self.raise_routing_exception(req)
        rule = req.url_rule
        # if we provide automatic options for this URL and the
        # request came with the OPTIONS method, reply automatically
        if (
            getattr(rule, "provide_automatic_options", False)
            and req.method == "OPTIONS"
        ):
            return self.make_default_options_response()
        # otherwise dispatch to the handler for that endpoint
        return self.view_functions[rule.endpoint](**req.view_args)

    def full_dispatch_request(self):
        """Dispatches the request and on top of that performs request
        pre and postprocessing as well as HTTP exception catching and
        error handling.

        .. versionadded:: 0.7
        """
        self.try_trigger_before_first_request_functions()
        try:
            request_started.send(self)
            rv = self.preprocess_request()
            if rv is None:
                rv = self.dispatch_request()
        except Exception as e:
            rv = self.handle_user_exception(e)
        return self.finalize_request(rv)

    def finalize_request(self, rv, from_error_handler=False):
        """Given the return value from a view function this finalizes
        the request by converting it into a response and invoking the
        postprocessing functions.  This is invoked for both normal
        request dispatching as well as error handlers.

        Because this means that it might be called as a result of a
        failure a special safe mode is available which can be enabled
        with the `from_error_handler` flag.  If enabled, failures in
        response processing will be logged and otherwise ignored.

        :internal:
        """
        response = self.make_response(rv)
        try:
            response = self.process_response(response)
            request_finished.send(self, response=response)
        except Exception:
            if not from_error_handler:
                raise
            self.logger.exception(
                "Request finalizing failed with an error while handling an error"
            )
        return response

    def try_trigger_before_first_request_functions(self):
        """Called before each request and will ensure that it triggers
        the :attr:`before_first_request_funcs` and only exactly once per
        application instance (which means process usually).

        :internal:
        """
        if self._got_first_request:
            return
        with self._before_request_lock:
            if self._got_first_request:
                return
            for func in self.before_first_request_funcs:
                func()
            self._got_first_request = True

    def make_default_options_response(self):
        """This method is called to create the default ``OPTIONS`` response.
        This can be changed through subclassing to change the default
        behavior of ``OPTIONS`` responses.

        .. versionadded:: 0.7
        """
        adapter = _request_ctx_stack.top.url_adapter
        methods = adapter.allowed_methods()
        rv = self.response_class()
        rv.allow.update(methods)
        return rv

    def should_ignore_error(self, error):
        """This is called to figure out if an error should be ignored
        or not as far as the teardown system is concerned.  If this
        function returns ``True`` then the teardown handlers will not be
        passed the error.

        .. versionadded:: 0.10
        """
        return False

    def make_response(self, rv):
        """Convert the return value from a view function to an instance of
        :attr:`response_class`.

        :param rv: the return value from the view function. The view function
            must return a response. Returning ``None``, or the view ending
            without returning, is not allowed. The following types are allowed
            for ``view_rv``:

            ``str``
                A response object is created with the string encoded to UTF-8
                as the body.

            ``bytes``
                A response object is created with the bytes as the body.

            ``dict``
                A dictionary that will be jsonify'd before being returned.

            ``tuple``
                Either ``(body, status, headers)``, ``(body, status)``, or
                ``(body, headers)``, where ``body`` is any of the other types
                allowed here, ``status`` is a string or an integer, and
                ``headers`` is a dictionary or a list of ``(key, value)``
                tuples. If ``body`` is a :attr:`response_class` instance,
                ``status`` overwrites the exiting value and ``headers`` are
                extended.

            :attr:`response_class`
                The object is returned unchanged.

            other :class:`~werkzeug.wrappers.Response` class
                The object is coerced to :attr:`response_class`.

            :func:`callable`
                The function is called as a WSGI application. The result is
                used to create a response object.

        .. versionchanged:: 0.9
           Previously a tuple was interpreted as the arguments for the
           response object.
        """

        status = headers = None

        # unpack tuple returns
        if isinstance(rv, tuple):
            len_rv = len(rv)

            # a 3-tuple is unpacked directly
            if len_rv == 3:
                rv, status, headers = rv
            # decide if a 2-tuple has status or headers
            elif len_rv == 2:
                if isinstance(rv[1], (Headers, dict, tuple, list)):
                    rv, headers = rv
                else:
                    rv, status = rv
            # other sized tuples are not allowed
            else:
                raise TypeError(
                    "The view function did not return a valid response tuple."
                    " The tuple must have the form (body, status, headers),"
                    " (body, status), or (body, headers)."
                )

        # the body must not be None
        if rv is None:
            raise TypeError(
                f"The view function for {request.endpoint!r} did not"
                " return a valid response. The function either returned"
                " None or ended without a return statement."
            )

        # make sure the body is an instance of the response class
        if not isinstance(rv, self.response_class):
            if isinstance(rv, (str, bytes, bytearray)):
                # let the response class set the status and headers instead of
                # waiting to do it manually, so that the class can handle any
                # special logic
                rv = self.response_class(rv, status=status, headers=headers)
                status = headers = None
            elif isinstance(rv, dict):
                rv = jsonify(rv)
            elif isinstance(rv, BaseResponse) or callable(rv):
                # evaluate a WSGI callable, or coerce a different response
                # class to the correct type
                try:
                    rv = self.response_class.force_type(rv, request.environ)
                except TypeError as e:
                    raise TypeError(
                        f"{e}\nThe view function did not return a valid"
                        " response. The return type must be a string,"
                        " dict, tuple, Response instance, or WSGI"
                        f" callable, but it was a {type(rv).__name__}."
                    ).with_traceback(sys.exc_info()[2])
            else:
                raise TypeError(
                    "The view function did not return a valid"
                    " response. The return type must be a string,"
                    " dict, tuple, Response instance, or WSGI"
                    f" callable, but it was a {type(rv).__name__}."
                )

        # prefer the status if it was provided
        if status is not None:
            if isinstance(status, (str, bytes, bytearray)):
                rv.status = status
            else:
                rv.status_code = status

        # extend existing headers with provided headers
        if headers:
            rv.headers.update(headers)

        return rv

    def create_url_adapter(self, request):
        """Creates a URL adapter for the given request. The URL adapter
        is created at a point where the request context is not yet set
        up so the request is passed explicitly.

        .. versionadded:: 0.6

        .. versionchanged:: 0.9
           This can now also be called without a request object when the
           URL adapter is created for the application context.

        .. versionchanged:: 1.0
            :data:`SERVER_NAME` no longer implicitly enables subdomain
            matching. Use :attr:`subdomain_matching` instead.
        """
        if request is not None:
            # If subdomain matching is disabled (the default), use the
            # default subdomain in all cases. This should be the default
            # in Werkzeug but it currently does not have that feature.
            if not self.subdomain_matching:
                subdomain = self.url_map.default_subdomain or None
            else:
                subdomain = None

            return self.url_map.bind_to_environ(
                request.environ,
                server_name=self.config["SERVER_NAME"],
                subdomain=subdomain,
            )
        # We need at the very least the server name to be set for this
        # to work.
        if self.config["SERVER_NAME"] is not None:
            return self.url_map.bind(
                self.config["SERVER_NAME"],
                script_name=self.config["APPLICATION_ROOT"],
                url_scheme=self.config["PREFERRED_URL_SCHEME"],
            )

    def inject_url_defaults(self, endpoint, values):
        """Injects the URL defaults for the given endpoint directly into
        the values dictionary passed.  This is used internally and
        automatically called on URL building.

        .. versionadded:: 0.7
        """
        funcs = self.url_default_functions.get(None, ())
        if "." in endpoint:
            bp = endpoint.rsplit(".", 1)[0]
            funcs = chain(funcs, self.url_default_functions.get(bp, ()))
        for func in funcs:
            func(endpoint, values)

    def handle_url_build_error(self, error, endpoint, values):
        """Handle :class:`~werkzeug.routing.BuildError` on
        :meth:`url_for`.
        """
        for handler in self.url_build_error_handlers:
            try:
                rv = handler(error, endpoint, values)
            except BuildError as e:
                # make error available outside except block
                error = e
            else:
                if rv is not None:
                    return rv

        # Re-raise if called with an active exception, otherwise raise
        # the passed in exception.
        if error is sys.exc_info()[1]:
            raise

        raise error

    def preprocess_request(self):
        """Called before the request is dispatched. Calls
        :attr:`url_value_preprocessors` registered with the app and the
        current blueprint (if any). Then calls :attr:`before_request_funcs`
        registered with the app and the blueprint.

        If any :meth:`before_request` handler returns a non-None value, the
        value is handled as if it was the return value from the view, and
        further request handling is stopped.
        """

        bp = _request_ctx_stack.top.request.blueprint

        funcs = self.url_value_preprocessors.get(None, ())
        if bp is not None and bp in self.url_value_preprocessors:
            funcs = chain(funcs, self.url_value_preprocessors[bp])
        for func in funcs:
            func(request.endpoint, request.view_args)

        funcs = self.before_request_funcs.get(None, ())
        if bp is not None and bp in self.before_request_funcs:
            funcs = chain(funcs, self.before_request_funcs[bp])
        for func in funcs:
            rv = func()
            if rv is not None:
                return rv

    def process_response(self, response):
        """Can be overridden in order to modify the response object
        before it's sent to the WSGI server.  By default this will
        call all the :meth:`after_request` decorated functions.

        .. versionchanged:: 0.5
           As of Flask 0.5 the functions registered for after request
           execution are called in reverse order of registration.

        :param response: a :attr:`response_class` object.
        :return: a new response object or the same, has to be an
                 instance of :attr:`response_class`.
        """
        ctx = _request_ctx_stack.top
        bp = ctx.request.blueprint
        funcs = ctx._after_request_functions
        if bp is not None and bp in self.after_request_funcs:
            funcs = chain(funcs, reversed(self.after_request_funcs[bp]))
        if None in self.after_request_funcs:
            funcs = chain(funcs, reversed(self.after_request_funcs[None]))
        for handler in funcs:
            response = handler(response)
        if not self.session_interface.is_null_session(ctx.session):
            self.session_interface.save_session(self, ctx.session, response)
        return response

    def do_teardown_request(self, exc=_sentinel):
        """Called after the request is dispatched and the response is
        returned, right before the request context is popped.

        This calls all functions decorated with
        :meth:`teardown_request`, and :meth:`Blueprint.teardown_request`
        if a blueprint handled the request. Finally, the
        :data:`request_tearing_down` signal is sent.

        This is called by
        :meth:`RequestContext.pop() <flask.ctx.RequestContext.pop>`,
        which may be delayed during testing to maintain access to
        resources.

        :param exc: An unhandled exception raised while dispatching the
            request. Detected from the current exception information if
            not passed. Passed to each teardown function.

        .. versionchanged:: 0.9
            Added the ``exc`` argument.
        """
        if exc is _sentinel:
            exc = sys.exc_info()[1]
        funcs = reversed(self.teardown_request_funcs.get(None, ()))
        bp = _request_ctx_stack.top.request.blueprint
        if bp is not None and bp in self.teardown_request_funcs:
            funcs = chain(funcs, reversed(self.teardown_request_funcs[bp]))
        for func in funcs:
            func(exc)
        request_tearing_down.send(self, exc=exc)

    def do_teardown_appcontext(self, exc=_sentinel):
        """Called right before the application context is popped.

        When handling a request, the application context is popped
        after the request context. See :meth:`do_teardown_request`.

        This calls all functions decorated with
        :meth:`teardown_appcontext`. Then the
        :data:`appcontext_tearing_down` signal is sent.

        This is called by
        :meth:`AppContext.pop() <flask.ctx.AppContext.pop>`.

        .. versionadded:: 0.9
        """
        if exc is _sentinel:
            exc = sys.exc_info()[1]
        for func in reversed(self.teardown_appcontext_funcs):
            func(exc)
        appcontext_tearing_down.send(self, exc=exc)

    def app_context(self):
        """Create an :class:`~flask.ctx.AppContext`. Use as a ``with``
        block to push the context, which will make :data:`current_app`
        point at this application.

        An application context is automatically pushed by
        :meth:`RequestContext.push() <flask.ctx.RequestContext.push>`
        when handling a request, and when running a CLI command. Use
        this to manually create a context outside of these situations.

        ::

            with app.app_context():
                init_db()

        See :doc:`/appcontext`.

        .. versionadded:: 0.9
        """
        return AppContext(self)

    def request_context(self, environ):
        """Create a :class:`~flask.ctx.RequestContext` representing a
        WSGI environment. Use a ``with`` block to push the context,
        which will make :data:`request` point at this request.

        See :doc:`/reqcontext`.

        Typically you should not call this from your own code. A request
        context is automatically pushed by the :meth:`wsgi_app` when
        handling a request. Use :meth:`test_request_context` to create
        an environment and context instead of this method.

        :param environ: a WSGI environment
        """
        return RequestContext(self, environ)

    def test_request_context(self, *args, **kwargs):
        """Create a :class:`~flask.ctx.RequestContext` for a WSGI
        environment created from the given values. This is mostly useful
        during testing, where you may want to run a function that uses
        request data without dispatching a full request.

        See :doc:`/reqcontext`.

        Use a ``with`` block to push the context, which will make
        :data:`request` point at the request for the created
        environment. ::

            with test_request_context(...):
                generate_report()

        When using the shell, it may be easier to push and pop the
        context manually to avoid indentation. ::

            ctx = app.test_request_context(...)
            ctx.push()
            ...
            ctx.pop()

        Takes the same arguments as Werkzeug's
        :class:`~werkzeug.test.EnvironBuilder`, with some defaults from
        the application. See the linked Werkzeug docs for most of the
        available arguments. Flask-specific behavior is listed here.

        :param path: URL path being requested.
        :param base_url: Base URL where the app is being served, which
            ``path`` is relative to. If not given, built from
            :data:`PREFERRED_URL_SCHEME`, ``subdomain``,
            :data:`SERVER_NAME`, and :data:`APPLICATION_ROOT`.
        :param subdomain: Subdomain name to append to
            :data:`SERVER_NAME`.
        :param url_scheme: Scheme to use instead of
            :data:`PREFERRED_URL_SCHEME`.
        :param data: The request body, either as a string or a dict of
            form keys and values.
        :param json: If given, this is serialized as JSON and passed as
            ``data``. Also defaults ``content_type`` to
            ``application/json``.
        :param args: other positional arguments passed to
            :class:`~werkzeug.test.EnvironBuilder`.
        :param kwargs: other keyword arguments passed to
            :class:`~werkzeug.test.EnvironBuilder`.
        """
        from .testing import EnvironBuilder

        builder = EnvironBuilder(self, *args, **kwargs)

        try:
            return self.request_context(builder.get_environ())
        finally:
            builder.close()

    def wsgi_app(self, environ, start_response):
        """The actual WSGI application. This is not implemented in
        :meth:`__call__` so that middlewares can be applied without
        losing a reference to the app object. Instead of doing this::

            app = MyMiddleware(app)

        It's a better idea to do this instead::

            app.wsgi_app = MyMiddleware(app.wsgi_app)

        Then you still have the original application object around and
        can continue to call methods on it.

        .. versionchanged:: 0.7
            Teardown events for the request and app contexts are called
            even if an unhandled error occurs. Other events may not be
            called depending on when an error occurs during dispatch.
            See :ref:`callbacks-and-errors`.

        :param environ: A WSGI environment.
        :param start_response: A callable accepting a status code,
            a list of headers, and an optional exception context to
            start the response.
        """
        ctx = self.request_context(environ)
        error = None
        try:
            try:
                ctx.push()
                response = self.full_dispatch_request()
            except Exception as e:
                error = e
                response = self.handle_exception(e)
            except:  # noqa: B001
                error = sys.exc_info()[1]
                raise
            return response(environ, start_response)
        finally:
            if self.should_ignore_error(error):
                error = None
            ctx.auto_pop(error)

    def __call__(self, environ, start_response):
        """The WSGI server calls the Flask application object as the
        WSGI application. This calls :meth:`wsgi_app` which can be
        wrapped to applying middleware."""
        return self.wsgi_app(environ, start_response)

    def __repr__(self):
        return f"<{type(self).__name__} {self.name!r}>"
