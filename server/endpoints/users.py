from flask import request, current_app
from flask_security import roles_accepted
from flask_security.utils import encrypt_password, verify_password
from server import forms
from server.return_codes import *


def read_all_users():
    from server.context import running_context

    @roles_accepted(*running_context.user_roles['/users'])
    def __func():
        result = str(running_context.User.query.all())
        return result, SUCCESS
    return __func()


def create_user(user_name):
    from server.context import running_context

    @roles_accepted(*running_context.user_roles['/users'])
    def __func():
        form = forms.NewUserForm(request.form)
        if not running_context.User.query.filter_by(email=user_name).first():
            un = user_name
            pw = encrypt_password(form.password.data)

            # Creates User
            u = running_context.user_datastore.create_user(email=un, password=pw)

            if form.role.data:
                u.set_roles(form.role.data)

            has_admin = False
            for role in u.roles:
                if role.name == 'admin':
                    has_admin = True
            if not has_admin:
                u.set_roles(['admin'])

            running_context.db.session.commit()
            current_app.logger.info('User added: {0}'.format(
                {"name": user_name, "roles": [str(_role) for _role in u.roles]}))
            return {}, OBJECT_CREATED
        else:
            current_app.logger.warning('Could not create user {0}. User already exists.'.format(user_name))
            return {"error": "User already exists.".format(user_name)}, OBJECT_EXISTS_ERROR
    return __func()


def read_user(user_name):
    from server.context import running_context

    @roles_accepted(*running_context.user_roles['/users'])
    def __func():
        user = running_context.user_datastore.get_user(user_name)
        if user:
            return user.display(), SUCCESS
        else:
            current_app.logger.error('Could not display user {0}. User does not exist.'.format(user_name))
            return {"error": 'User does not exist.'.format(user_name)}, OBJECT_DNE_ERROR
    return __func()


def update_user(user_name):
    from server.context import running_context

    @roles_accepted(*running_context.user_roles['/users'])
    def __func():
        user = running_context.user_datastore.get_user(user_name)
        if user:
            form = forms.EditUserForm(request.form)
            if form.new_password and form.old_password:
                if verify_password(form.old_password.data, user.password):
                    user.password = encrypt_password(form.new_password.data)
                else:
                    current_app.logger.error('Could not edit user {0}. Current user password was entered incorrectly.'.format(user_name))
                    return {"error": 'Current user password was entered incorrectly.'.format(user_name)}, INVALID_INPUT_ERROR
            if form.roles.data:
                user.set_roles(form.roles.data)
            running_context.db.session.commit()
            current_app.logger.info('Updated user {0}. Roles: {1}'.format(user_name, form.roles.data))
            return user.display(), SUCCESS
        else:
            current_app.logger.error('Could not edit user {0}. User does not exist.'.format(user_name))
            return {"error": 'User does not exist.'.format(user_name)}, OBJECT_DNE_ERROR
    return __func()


def delete_user(user_name):
    from server.flaskserver import running_context, current_user

    @roles_accepted(*running_context.user_roles['/users'])
    def __func():
        user = running_context.user_datastore.get_user(user_name)
        if user:
            if user != current_user:
                running_context.user_datastore.delete_user(user)
                running_context.db.session.commit()
                current_app.logger.info('User {0} deleted'.format(user_name))
                return {}, SUCCESS
            else:
                current_app.logger.error('Could not delete user {0}. User is current user.'.format(user_name))
                return {"error": 'User is current user.'.format(user_name)}, UNAUTHORIZED_ERROR
        else:
            current_app.logger.error('Could not delete user {0}. User does not exist.'.format(user_name))
            return {"error": 'User does not exist.'.format(user_name)}, OBJECT_DNE_ERROR
    return __func()
