import { FC } from 'react';
import './ProgressBar.scss';

interface ProgressBarProps {
  title: string;
  progress: number;
  className?: string;
}

const ProgressBar: FC<ProgressBarProps> = ({
  title,
  progress,
  className = '',
}) => {
  return (
    <div className={`progress-bar ${className}`}>
      <h2>{title}</h2>
      <div className="progress-bar__container">
        <div className="progress-bar__track">
          <div
            className="progress-bar__fill"
            style={{ width: `${progress}%` }}
          />
        </div>
        <span className="progress-bar__text">{progress}%</span>
      </div>
    </div>
  );
};

export default ProgressBar;
