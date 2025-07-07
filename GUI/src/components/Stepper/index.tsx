import { FC } from 'react';
import './Stepper.scss';

interface Step {
  id: number;
  label: string;
  active?: boolean;
  completed?: boolean;
}

interface StepperProps {
  steps: Step[];
  currentStep: number;
}

const Stepper: FC<StepperProps> = ({ steps, currentStep }) => {
  return (
    <div className="stepper">
      {steps.map((step) => (
        <div
          key={step.id}
          className={`stepper__step ${
            step.id === currentStep ? 'stepper__step--active' : ''
          } ${step.id < currentStep ? 'stepper__step--completed' : ''}`}
        >
          <span className="stepper__number">{step.id}</span>
          <span className="stepper__label">{step.label}</span>
        </div>
      ))}
    </div>
  );
};

export default Stepper;
