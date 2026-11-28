import * as React from 'react';
import {cn} from '@/lib/utils';

// shadcn/ui · card
const Card = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(({className, ...props}, ref) => (
  <div ref={ref} className={cn('min-w-0 rounded-lg border bg-card text-card-foreground shadow-sm', className)} {...props} />
));
Card.displayName = 'Card';

const CardHeader = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(({className, ...props}, ref) => (
  <div ref={ref} className={cn('flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 px-5 pt-3.5 mob:px-3', className)} {...props} />
));
CardHeader.displayName = 'CardHeader';

const CardTitle = React.forwardRef<HTMLElement, React.HTMLAttributes<HTMLElement>>(({className, ...props}, ref) => (
  <b ref={ref} className={cn('text-sm font-semibold', className)} {...props} />
));
CardTitle.displayName = 'CardTitle';

const CardContent = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(({className, ...props}, ref) => (
  <div ref={ref} className={cn('px-5 pb-[18px] pt-3 mob:px-3 mob:pb-3.5', className)} {...props} />
));
CardContent.displayName = 'CardContent';

export {Card, CardHeader, CardTitle, CardContent};
