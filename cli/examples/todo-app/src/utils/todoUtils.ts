import { Todo } from '../types';

export function generateId(): string {
  return Math.random().toString(36).substr(2, 9);
}

export function filterTodos(todos: Todo[], filter: string): Todo[] {
  switch (filter) {
    case 'active':
      return todos.filter(todo => !todo.completed);
    case 'completed':
      return todos.filter(todo => todo.completed);
    default:
      return todos;
  }
}

export function sortTodosByDate(todos: Todo[]): Todo[] {
  return [...todos].sort((a, b) =>
    b.createdAt.getTime() - a.createdAt.getTime()
  );
}