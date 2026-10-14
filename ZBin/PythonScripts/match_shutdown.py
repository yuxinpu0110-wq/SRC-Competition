"""独立尝试每项退出清理；一项失败不能跳过其余停车/关闭步骤。"""


def cleanup(actions):
    errors=[]
    for name,action in actions:
        try:action()
        except Exception as exc:
            errors.append((name,exc))
    return errors
