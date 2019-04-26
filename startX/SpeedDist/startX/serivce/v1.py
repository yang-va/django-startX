from django.urls import path, re_path
from django.shortcuts import HttpResponse, render, reverse, redirect
from types import FunctionType
from django.utils.safestring import mark_safe
from startX.utils.pagination import Pagination
from django.http import QueryDict
import functools
from django import forms
from django.db.models import Q
from django.db.models import ForeignKey, ManyToManyField


def get_field_display(field_title, field):
    """

    :param field_title: 数据库表字段希望显示的表头
    :param field: 数据库表字段
    :return:
    """

    def inner(self, model=None, is_header=None):
        if is_header:
            return field_title
        return getattr(model, 'get_%s_display' % field)

    return inner


class SearchGroupRow(object):
    def __init__(self, title, queryset_or_tuple, option, query_dict):
        """

        :param title: 组合搜索的列名称
        :param queryset_or_tuple: 组合搜索关联获取到的数据
        :param option: 配置
        :param query_dict: request.GET
        """
        self.title = title
        self.queryset_or_tuple = queryset_or_tuple
        self.option = option
        self.query_dict = query_dict

    def __iter__(self):
        yield '<div class="whole">'
        yield self.title
        yield '</div>'
        yield '<div class="others">'
        total_query_dict = self.query_dict.copy()
        total_query_dict._mutable = True

        origin_value_list = self.query_dict.getlist(self.option.field)
        if not origin_value_list:
            yield "<a class='active' href='?%s'>全部</a>" % total_query_dict.urlencode()
        else:
            total_query_dict.pop(self.option.field)
            yield "<a href='?%s'>全部</a>" % total_query_dict.urlencode()

        for item in self.queryset_or_tuple:
            text = self.option.get_text(item)
            value = str(self.option.get_value(item))
            query_dict = self.query_dict.copy()
            query_dict._mutable = True

            if not self.option.is_multi:
                query_dict[self.option.field] = value
                if value in origin_value_list:
                    query_dict.pop(self.option.field)
                    yield "<a class='active' href='?%s'>%s</a>" % (query_dict.urlencode(), text)
                else:
                    yield "<a href='?%s'>%s</a>" % (query_dict.urlencode(), text)
            else:
                multi_value_list = query_dict.getlist(self.option.field)
                if value in multi_value_list:
                    multi_value_list.remove(value)
                    query_dict.setlist(self.option.field, multi_value_list)
                    yield "<a class='active' href='?%s'>%s</a>" % (query_dict.urlencode(), text)
                else:
                    multi_value_list.append(value)
                    query_dict.setlist(self.option.field, multi_value_list)
                    yield "<a href='?%s'>%s</a>" % (query_dict.urlencode(), text)

        yield '</div>'


class Option(object):
    def __init__(self, field, is_multi=False, db_condition=None, text_func=None, value_func=None):
        """
        :param field: 组合搜索关联的字段
        :param is_multi: 是否支持多选
        :param db_condition: 数据库关联查询时的条件
        :param text_func: 此函数用于显示组合搜索按钮页面文本
        :param value_func: 此函数用于显示组合搜索按钮值
        """
        self.field = field
        self.is_multi = is_multi
        if not db_condition:
            db_condition = {}
        self.db_condition = db_condition
        self.text_func = text_func
        self.value_func = value_func

        self.is_choice = False

    def get_db_condition(self, request, *args, **kwargs):
        return self.db_condition

    def get_queryset_or_tuple(self, model_class, request, *args, **kwargs):
        """
        根据字段去获取数据库关联的数据
        :return:
        """
        # 根据gender或depart字符串，去自己对应的Model类中找到字段对象
        field_object = model_class._meta.get_field(self.field)
        title = field_object.verbose_name
        # 获取关联数据
        if isinstance(field_object, ForeignKey) or isinstance(field_object, ManyToManyField):
            # FK和M2M,应该去获取其关联表中的数据： QuerySet
            db_condition = self.get_db_condition(request, *args, **kwargs)

            # django1写法
            # return SearchGroupRow(title, field_object.rel.model.objects.filter(**db_condition), self)

            # django2写法
            return SearchGroupRow(title, field_object.related_model.objects.filter(**db_condition), self, request.GET)
        else:
            # 获取choice中的数据：元组
            self.is_choice = True
            return SearchGroupRow(title, field_object.choices, self, request.GET)

    def get_text(self, field_object):
        """
        获取文本函数
        :param field_object:
        :return:
        """
        if self.text_func:
            return self.text_func(field_object)

        if self.is_choice:
            return field_object[1]

        return str(field_object)

    def get_value(self, field_object):
        if self.value_func:
            return self.value_func(field_object)

        if self.is_choice:
            return field_object[0]

        return field_object.pk


class StartXModelForm(forms.ModelForm):
    """为数据库统一生成ModelForm的字段添加样式表"""

    def __init__(self, *args, **kwargs):
        super(StartXModelForm, self).__init__(*args, **kwargs)

        for name, field in self.fields.items():
            field.widget.attrs['class'] = 'form-control'


class StartXHandler(object):
    list_display = []  # 数据表的字段
    per_page_count = 10  # 分页器每页显示数量
    has_add_btn = False  # 添加按钮
    model_form_class = None  # 数据库表的form
    order_by = None  # 排序参数
    search_list = []  # 搜索关键词
    action_list = []  # 批量操作字段
    search_group = []  # 组合搜索字段

    def __init__(self, site, model_class, prev):
        self.model_class = model_class  # 表对象
        self.prev = prev  # url后缀
        self.site = site
        self.request = None  # 设置一个统一的request请求体

    def get_search_group(self):
        return self.search_group

    def display_edit(self, model=None, is_header=None):
        """

        :param model: model即数据库表对象
        :param is_header: 是否是表头字段
        :return: 显示除了表的字段verbose_name外，自添加字段
        """

        if is_header:
            return '操作'

        url_name = reverse('%s:%s' % (self.site.namespace, self.get_change_name), args=(model.pk,))
        return mark_safe('<a href="%s">编辑</a>' % url_name)

    def display_del(self, model=None, is_header=None):
        """

        :param model: model即数据库表对象
        :param is_header: 是否是表头字段
        :return: 显示除了表的字段verbose_name外，自添加字段
        """

        if is_header:
            return '操作'

        url_name = reverse('%s:%s' % (self.site.namespace, self.get_del_name), args=(model.pk,))

        return mark_safe('<a href="%s">删除</a>' % url_name)

    def display_checkbox(self, model=None, is_header=None):
        """
        批量操作前面的checkbox按钮
        :param obj:
        :param is_header:
        :return:
        """
        if is_header:
            return "选择"
        return mark_safe('<input type="checkbox" name="pk" value="%s" />' % model.pk)

    def action_multi_delete(self, request, *args, **kwargs):
        """
        批量删除钩子，如果想要定制执行成功后的返回值，那么就为action函数设置返回值即可。
        :return:
        """
        pk_list = request.POST.getlist('pk')
        self.model_class.objects.filter(id__in=pk_list).delete()

    action_multi_delete.text = "批量删除"

    def get_list_display(self):
        """
        预留的钩子函数
        :return: 为不同权限的用户设置预留的扩展，自定义显示列
        """
        value = []
        value.extend(self.list_display)
        return value

    def get_add_btn(self):
        """
        添加按钮
        :return:
        """
        if self.has_add_btn:
            return '<a class="btn btn-success" href="%s">添加</a>' % self.reverse_add_url()
        return None

    def get_model_form(self):
        if self.model_form_class:
            return self.model_form_class

        class DynamicModelForm(StartXModelForm):
            """"默认显示全部的字段"""

            class Meta:
                model = self.model_class
                fields = "__all__"

        return DynamicModelForm

    def get_order_by(self):
        """
        预留的排序钩子,如果子类有设置order_by参数就用子类的，没有就用默认的
        :return:
        """
        return self.order_by or ['id']

    def get_search_list(self):
        """
        预留的搜索钩子
        :return:
        """
        return self.search_list

    def get_action_list(self):
        """
        批量操作字段
        :return:
        """
        return self.action_list

    def get_search_group_condition(self, request):
        """
        获取组合搜索条件
        :param request:
        :return:
        """

        condition = {}
        for option in self.get_search_group():
            values_list = request.GET.getlist(option.field)
            if not values_list:
                continue
            condition['%s__in' % option.field] = values_list
        print(condition)
        return condition

    def reverse_add_url(self):
        """
        反向解析添加的url
        :return:
        """
        base_url = reverse('%s:%s' % (self.site.namespace, self.get_add_name))
        if not self.request.GET:
            return base_url

        # 记住request的参数
        params = self.request.GET.urlencode()
        query_dict = QueryDict(mutable=True)
        query_dict['_filter'] = params
        query_url = '%s?%s' % (base_url, query_dict.urlencode())
        return "%s?%s" % (query_url, params,)

    def reverse_list_url(self):
        """
        反向解析列表的url
        :return:
        """
        base_url = reverse('%s:%s' % (self.site.namespace, self.get_list_name))

        # 记住request的参数
        params = self.request.GET.get('_filter')
        if not params:
            return base_url

        return "%s?%s" % (base_url, params,)

    def wrapper(self, func):
        @functools.wraps(func)
        def inner(request, *args, **kwargs):
            self.request = request
            return func(request, *args, **kwargs)

        return inner

    def changelist(self, request, *args, **kwargs):
        """

        :param request:
        :return:
        """

        # ########## 1. 批量操作 ##########
        action_list = self.get_action_list()
        action_dict = {func.__name__: func.text for func in action_list}

        if request.method == 'POST':
            action_func_name = request.POST.get('action')
            if action_func_name and action_func_name in action_dict:
                func = getattr(self, action_func_name)
                actioin_response = func(request, *args, **kwargs)
                if actioin_response:
                    return redirect(actioin_response)

        # ########## 2. 搜索 ##########

        search_list = self.get_search_list()
        query_field = request.GET.get('q', '')
        conn = Q()
        conn.connector = 'OR'
        if query_field:
            for field in search_list:
                conn.children.append((field, query_field))
        # ########## 3. 排序 ##########
        order_by_field = self.get_order_by()
        # ########## 4. 组合搜索结果 ##########
        search_group_condition = self.get_search_group_condition(request)
        querySet = self.model_class.objects.filter(conn).filter(**search_group_condition).order_by(*order_by_field)

        # ########## 5. 处理分页 ##########
        all_count = querySet.count()
        query_params = request.GET.copy()
        query_params._mutable = True  # 设置该属性才可以修改request.GET

        pager = Pagination(
            current_page=request.GET.get('page'),
            all_count=all_count,
            base_url=request.path_info,
            query_params=query_params,
            per_page=self.per_page_count,
        )

        # 已经分页出处理好的数据
        data_list = querySet[pager.start:pager.end]

        # ########## 6. 处理表格 ##########

        # 处理表头
        list_display = self.get_list_display()
        header_list = []

        if list_display:
            for key_or_func in list_display:
                if isinstance(key_or_func, FunctionType):
                    verbose_name = key_or_func(self, model=None, is_header=True)
                else:
                    verbose_name = self.model_class._meta.get_field(key_or_func).verbose_name
                header_list.append(verbose_name)
        else:
            header_list.append(self.model_class._meta.model_name)  # 如果是个model对象，直接显示对象，在models文件里定义对象的__str__方法即可

        # 根据表头处理表内容

        body_list = []
        for item in data_list:
            tr_list = []
            if list_display:
                for key_or_func in list_display:
                    if isinstance(key_or_func, FunctionType):
                        tr_list.append(key_or_func(self, model=item, is_header=False))
                    else:
                        tr_list.append(getattr(item, key_or_func))
            else:
                tr_list.append(item)
            body_list.append(tr_list)

        # ########## 7. 组合搜索 ##########

        search_group = self.get_search_group()
        search_group_row_list = []
        for option_object in search_group:
            row = option_object.get_queryset_or_tuple(self.model_class, request, *args, **kwargs)
            search_group_row_list.append(row)

        return render(request,
                      'startX/list.html',
                      {
                          'data_list': data_list,
                          'header_list': header_list,
                          'body_list': body_list,
                          'pager': pager,
                          'add_btn': self.get_add_btn(),  # 添加按钮
                          'search_list': search_list,  # 搜索字段范围集
                          'query_field': query_field,  # 搜索字段条件
                          'action_dict': action_dict,
                          'search_group_row_list': search_group_row_list
                      })

    def save(self, form, is_update=False):
        """
        预留钩子函数
        :param form:
        :param is_update:
        :return:
        """
        form.save()

    def add_view(self, request, *args, **kwargs):
        """

        :param request:
        :return:
        """
        model_class_form = self.get_model_form()

        if request.method == 'GET':
            form = model_class_form()
            return render(request, 'startX/change.html', {'form': form})
        form = model_class_form(data=request.POST)
        if form.is_valid():
            self.save(form, is_update=False)
            # 在数据库保存成功后，跳转回列表页面(携带原来的参数)。
            return redirect(self.reverse_list_url())
        return HttpResponse('<h2>添加页面</h2>')

    def change_view(self, request, pk, *args, **kwargs):
        """

        :param request:
        :return:
        """

        current_model_object = self.model_class.objects.filter(pk=pk).first()
        if not current_model_object:
            return HttpResponse('当前选择的对象不存在，请重试')
        model_form_class = self.get_model_form()
        if request.method == 'GET':
            form = model_form_class(instance=current_model_object)
            return render(request, 'startX/change.html', {'form': form})

        form = model_form_class(data=request.POST, instance=current_model_object)

        if form.is_valid():
            self.save(form, is_update=False)
            # 在数据库保存成功后，跳转回列表页面(携带原来的参数)。
            return redirect(self.reverse_list_url())
        return render(request, 'startX/change.html', {'form': form})

    def delete_view(self, request, pk, *args, **kwargs):
        """

        :param request:
        :return:
        """

        current_model_object = self.model_class.objects.filter(pk=pk).first()
        if not current_model_object:
            return HttpResponse('当前选择的对象不存在，请重试')

        cancel_url = self.reverse_list_url()
        if request.method == 'GET':
            return render(request, 'startX/delete.html', {'cancel': cancel_url})

        self.model_class.objects.filter(pk=pk).delete()
        return redirect(cancel_url)

    def get_url_name(self, params):
        """

        :param params: 参数
        :return: 公用的设置url别名方法
        """
        app_label, model_name = self.model_class._meta.app_label, self.model_class._meta.model_name
        if self.prev:
            return '%s_%s_%s_%s' % (app_label, model_name, self.prev, params)
        return '%s_%s_%s' % (app_label, model_name, params)

    @property
    def get_list_name(self):
        return self.get_url_name('list')

    @property
    def get_add_name(self):
        return self.get_url_name('add')

    @property
    def get_change_name(self):
        return self.get_url_name('change')

    @property
    def get_del_name(self):
        return self.get_url_name('del')

    def get_urls(self):
        """预留的重新自定义url钩子函数,主要是覆盖掉默认的url,并设置name别名"""

        patterns = [
            re_path(r'^list/$', self.wrapper(self.changelist), name=self.get_list_name),
            re_path(r'^add/$', self.wrapper(self.add_view), name=self.get_add_name),
            re_path(r'^change/(?P<pk>\d+)/$', self.wrapper(self.change_view), name=self.get_change_name),
            re_path(r'^del/(?P<pk>\d+)/$', self.wrapper(self.delete_view), name=self.get_del_name)

        ]
        patterns.extend(self.extra_url())
        return patterns

    def extra_url(self):
        """预留的重新自定义url钩子函数,主要是在原有基础上增加url，对应的视图也需要自定义，
            extra_url原则上不能和get_url同时使用
        """
        return []


class StartXSite(object):
    def __init__(self):
        self._registry = []
        self.app_name = 'startX'
        self.namespace = 'startX'

    def register(self, model_class, handler_class=None, prev=None):
        """

        :param model_class: app的表对象
        :param handler_class: 对应url的视图类
        :return:

            [
                "model_class":models.Depart,"handler":DepartHandler(models.Depart)
                "model_class":models.UserInfo,"handler":UserInfoHandler(models.UserInfo)
                "model_class":models.Host,"handler":HostHandler(models.Host)
            ]

        """
        if not handler_class:
            handler_class = StartXHandler
        self._registry.append(
            {'model_class': model_class, 'handler_class': handler_class(self, model_class, prev), 'prev': prev})

    def get_urls(self):
        patterns = []

        for item in self._registry:
            model_class = item['model_class']
            handler_class = item['handler_class']
            prev = item['prev']
            app_label, model_name = model_class._meta.app_label, model_class._meta.model_name

            if prev:
                # patterns.append(re_path(r'^%s/%s/%s/list/$' % (app_label, model_name,prev), handler_class.changelist))
                # patterns.append(re_path(r'^%s/%s/%s/add/$' % (app_label, model_name,prev), handler_class.add_view))
                # patterns.append(re_path(r'^%s/%s/%s/change/(\d+)/$' % (app_label, model_name,prev), handler_class.change_view))
                # patterns.append(re_path(r'^%s/%s/%s/del/(\d+)/$' % (app_label, model_name,prev), handler_class.delete_view))
                patterns.append(
                    re_path(r'^%s/%s/%s/' % (app_label, model_name, prev), (handler_class.get_urls(), None, None)))
            else:
                # patterns.append(re_path(r'^%s/%s/list/$' % (app_label, model_name), handler_class.changelist))
                # patterns.append(re_path(r'^%s/%s/add/$' % (app_label, model_name), handler_class.add_view))
                # patterns.append(re_path(r'^%s/%s/change/(\d+)/$' % (app_label, model_name), handler_class.change_view))
                # patterns.append(re_path(r'^%s/%s/del/(\d+)/$' % (app_label, model_name), handler_class.delete_view))

                patterns.append(
                    re_path(r'^%s/%s/' % (app_label, model_name), (handler_class.get_urls(), None, None)))
        return patterns

    @property
    def urls(self):
        return self.get_urls(), self.app_name, self.namespace


site = StartXSite()
