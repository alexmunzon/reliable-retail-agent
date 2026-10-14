"""Passive actor dispatch observer, outside unchanged native scorer replay."""
from copy import deepcopy

class DispatchObserver:
    def __init__(self, env):
        self.native_env = env
        self.dispatches = []
        self.native_done = False
        self.pre_terminal_data = None

    def __getattr__(self, name):
        return getattr(self.native_env, name)

    def reset(self, index=None):
        self.dispatches = []
        self.native_done = False
        self.pre_terminal_data = None
        return self.native_env.reset(index=index)

    def step(self, action):
        before = deepcopy(self.native_env.data)
        record = {'sequence': len(self.dispatches)+1, 'action': deepcopy(action),
                  'data_hash_before': self.native_env.get_data_hash()}
        self.dispatches.append(record)
        observation, reward, done, info = self.native_env.step(action)
        record.update(observation=observation, reward=reward, done=done)
        self.native_done = done
        if done:
            # This synthetic fixture terminates through respond; no terminal writes.
            self.pre_terminal_data = before
        return observation, reward, done, info
